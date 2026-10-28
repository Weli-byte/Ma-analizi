"""As-of safe xG/xGA features (ADR 0085). Everything is computed for ONE prediction cutoff.

A match enters a team's history only if it is OBSERVED, kicked off before the cutoff AND its xG was available (`available_at_utc`, the fetch time) at or before the cutoff; the xG of the
target fixture itself is excluded by key as well. Fewer than MIN_OBS observations gives NaN plus `xg_available=False` and a reason, never a zero. The features are behind
`xg_features_enabled()` (env XG_FEATURES_ENABLED=1), OFF by default: `fixture_features` returns None while it is off unless an experiment passes `force=True`.
"""

import math
import os
from datetime import datetime, timedelta
from statistics import fmean

from .observations import OBSERVED, Observation

FEATURE_VERSION = "xg-v1"
WINDOWS = (3, 5, 10)
EWMA_HALF_LIFE_MATCHES = 5.0
MIN_OBS = 3                      # fewer observations than this: NaN, not a number built on one match
COVERAGE_LOOKBACK = 10           # coverage_pct = observed share of the team's last finished matches
RESULT_LAG = timedelta(hours=3)  # same inferred result availability as ADR 0006
NAN = float("nan")


def xg_features_enabled(environ=None) -> bool:
    return (os.environ if environ is None else environ).get("XG_FEATURES_ENABLED", "0") == "1"


def _eligible(rows: list[Observation], cutoff: datetime, exclude_key: str | None) -> list[Observation]:
    return [
        o for o in rows
        if o.coverage_status == OBSERVED and o.kickoff_utc < cutoff and o.available_at_utc is not None and o.available_at_utc <= cutoff and o.fixture_key != exclude_key
    ]  # fmt: skip


def _side(o: Observation, team: str) -> tuple[float, float, str, str]:
    """(xg_for, xg_against, venue, opponent) of `team` in this observation."""
    if o.home_id == team:
        return o.xg_home, o.xg_away, "home", o.away_id
    return o.xg_away, o.xg_home, "away", o.home_id


def _mean(xs: list[float]) -> float:
    return fmean(xs) if len(xs) >= MIN_OBS else NAN


def _ewma(xs: list[float]) -> float:
    """Exponentially weighted mean, newest last; weight halves every EWMA_HALF_LIFE_MATCHES matches."""
    if len(xs) < MIN_OBS:
        return NAN
    w = [0.5 ** ((len(xs) - 1 - i) / EWMA_HALF_LIFE_MATCHES) for i in range(len(xs))]
    return sum(a * b for a, b in zip(w, xs, strict=True)) / sum(w)


def _conceded_means(elig: list[Observation]) -> tuple[dict[str, float], float]:
    """Mean xG conceded per team (needs MIN_OBS matches) and the league-wide mean, from the same as-of rows."""
    per: dict[str, list[float]] = {}
    for o in elig:
        per.setdefault(o.home_id, []).append(o.xg_away)
        per.setdefault(o.away_id, []).append(o.xg_home)
    allv = [x for v in per.values() for x in v]
    return {t: fmean(v) for t, v in per.items() if len(v) >= MIN_OBS}, (fmean(allv) if allv else NAN)


def team_features(rows: list[Observation], team: str, cutoff: datetime, exclude_key: str | None = None) -> dict:
    elig = _eligible(rows, cutoff, exclude_key)
    mine = sorted((o for o in elig if team in (o.home_id, o.away_id)), key=lambda o: (o.kickoff_utc, o.source_match_id))
    conceded, league_mean = _conceded_means(elig)
    sides = [_side(o, team) for o in mine]
    out: dict = {"n_obs": len(mine), "last_available_at": max((o.available_at_utc for o in mine), default=None)}
    for k in WINDOWS:
        tail = sides[-k:]
        out[f"xg_for_r{k}"] = _mean([s[0] for s in tail])
        out[f"xg_against_r{k}"] = _mean([s[1] for s in tail])
    out["xg_for_ewma"] = _ewma([s[0] for s in sides])
    out["xg_against_ewma"] = _ewma([s[1] for s in sides])
    for venue in ("home", "away"):
        v = [s for s in sides if s[2] == venue][-10:]
        out[f"xg_for_{venue}"] = _mean([s[0] for s in v])
        out[f"xg_against_{venue}"] = _mean([s[1] for s in v])
    tail = sides[-10:]  # opponent strength adjustment: xG scored minus what that opponent concedes on average (league mean when the opponent has too little data), both as of the cutoff
    out["xg_for_opp_adj_r10"] = _mean([s[0] - conceded.get(s[3], league_mean) for s in tail]) if not math.isnan(league_mean) else NAN
    played = sorted((o for o in rows if team in (o.home_id, o.away_id) and o.kickoff_utc + RESULT_LAG <= cutoff), key=lambda o: o.kickoff_utc)[-COVERAGE_LOOKBACK:]
    keys = {o.source_match_id for o in elig}
    got = sum(1 for o in played if o.source_match_id in keys)
    out["coverage_pct"] = (100.0 * got / len(played)) if played else NAN
    ok = len(mine) >= MIN_OBS
    out["xg_available"] = ok
    out["xg_missing_reason"] = None if ok else ("NO_OBSERVATION_AVAILABLE_AT_CUTOFF" if not mine else f"FEWER_THAN_{MIN_OBS}_OBSERVATIONS")
    return out


def fixture_features(rows: list[Observation], home: str, away: str, cutoff: datetime, fixture_key: str | None = None, force: bool = False) -> dict | None:
    """Features of both sides for one prediction cutoff, or None while the flag is off (a consumer must handle None = no xG features)."""
    if not (force or xg_features_enabled()):
        return None
    return {"feature_version": FEATURE_VERSION, "cutoff_utc": cutoff.isoformat(), "home": team_features(rows, home, cutoff, fixture_key), "away": team_features(rows, away, cutoff, fixture_key)}
