"""Sprint 4 (ADR 0066): the single feature contract of every gradient-boosting candidate, global and local.

One chronological pass turns finished matches into as-of feature rows. A match enters the state only when it is AVAILABLE at
the stage cutoff of the row being built (kick-off + the inferred result lag <= kick-off - stage offset), so a result published
after the cutoff can never reach a feature; fixtures sharing a kick-off never see each other. The same code builds training and
evaluation rows, so there is no second "serving" implementation that could drift (train/serve skew is checked by comparing the
schema hash and by perturbing the future, see tests and `perturbation_audit`).

Score-only universe: there are no odds, xG, lineups or shots in the licensed data, so those features do not exist (they are not
filled with zeros). A value that cannot be computed is NaN together with an `_available` flag.

Strength features come from the Sprint 3 `TimeAwareElo` driven match by match with the same availability rule (its private
`_diff` / `_update` are used inside the package for that; a test pins the equivalence with its public replay)."""

import bisect
import dataclasses
import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from src.evaluation.dataset import EvalRow
from src.snapshot.stages import SnapshotStage, cutoff_for_stage

from .stat_models import EloSpec, TimeAwareElo

SCHEMA_VERSION = "lm-s4-features-v1"
UEFA_IDS = frozenset({"UEFA_UCL", "UEFA_UEL", "UEFA_UECL"})

# (name, group). The order here IS the column order of every model, global or local.
_FORM = ("gf", "ga", "pts")


def _schema(windows: tuple[int, ...]) -> list[tuple[str, str]]:
    cols: list[tuple[str, str]] = [
        ("league_code", "league_identity"), ("league_known", "league_identity"), ("is_uefa", "league_identity"),
        ("lp_goals_per_match", "league_priors"), ("lp_home_win_rate", "league_priors"), ("lp_draw_rate", "league_priors"),
        ("lp_over25_rate", "league_priors"), ("lp_btts_rate", "league_priors"), ("lp_available", "league_priors"),
        ("lp_home_advantage", "league_priors"),
        ("elo_diff", "elo"), ("elo_min_matches", "elo"), ("elo_certain", "elo"),
    ]  # fmt: skip
    for side in ("h", "a"):
        for w in windows:
            for k in _FORM:
                if k == "pts" and w != windows[0]:
                    continue
                cols.append((f"{side}_{k}{w}", "form"))
    cols.append(("form_available", "form"))
    for side, venue in (("h", "home"), ("a", "away")):
        cols += [(f"{side}_{venue}_gf5", "home_away"), (f"{side}_{venue}_ga5", "home_away")]
    cols += [("h_rest_days", "rest_congestion"), ("a_rest_days", "rest_congestion"), ("rest_diff", "rest_congestion"),
             ("rest_available", "rest_congestion"), ("h_matches_30d", "rest_congestion"), ("a_matches_30d", "rest_congestion")]  # fmt: skip
    cols += [("h_prior_matches", "team_status"), ("a_prior_matches", "team_status"), ("either_new_team", "team_status"),
             ("days_into_season", "calendar")]  # fmt: skip
    return cols


@dataclass(frozen=True)
class FeatureSchema:
    names: tuple[str, ...]
    groups: dict[str, str]
    version: str
    sha256: str

    def index(self, name: str) -> int:
        return self.names.index(name)

    def columns_of(self, *groups: str) -> list[int]:
        return [i for i, n in enumerate(self.names) if self.groups[n] in groups]

    def without_groups(self, *groups: str) -> list[int]:
        return [i for i, n in enumerate(self.names) if self.groups[n] not in groups]

    def as_dict(self) -> dict:
        return {"version": self.version, "sha256": self.sha256, "columns": [{"name": n, "group": self.groups[n]} for n in self.names],
                "not_available_in_the_licensed_universe": ["bookmaker odds", "xG", "lineups", "shots / corners / cards", "injuries"]}  # fmt: skip


def make_schema(windows: tuple[int, ...]) -> FeatureSchema:
    cols = _schema(windows)
    names = tuple(n for n, _ in cols)
    if len(set(names)) != len(names):
        raise ValueError("duplicate feature name in the schema")
    groups = dict(cols)
    digest = hashlib.sha256(json.dumps([SCHEMA_VERSION, cols], sort_keys=False).encode("utf-8")).hexdigest()
    return FeatureSchema(names, groups, SCHEMA_VERSION, digest)


@dataclass(frozen=True)
class FeatureConfig:
    vocabulary: tuple[str, ...]
    windows: tuple[int, ...]
    min_form_matches: int
    rest_cap_days: float
    congestion_days: int
    prior_window_matches: int
    min_prior_matches: int
    new_team_matches: int
    lag_hours: int
    stage: SnapshotStage
    elo: EloSpec

    @staticmethod
    def from_raw(raw: dict, vocabulary, lag_hours: int, stage: SnapshotStage, elo: EloSpec) -> "FeatureConfig":
        f = raw["features"]
        return FeatureConfig(tuple(vocabulary), tuple(f["form_windows"]), int(f["min_form_matches"]), float(f["rest_cap_days"]),
                             int(f["congestion_days"]), int(f["prior_window_matches"]), int(f["min_prior_matches"]),
                             int(f["new_team_matches"]), lag_hours, stage, elo)  # fmt: skip


@dataclass
class FeatureMatrix:
    schema: FeatureSchema
    X: np.ndarray                       # (n, p) float64, NaN = not available
    fixture_ids: list[str]
    unseen_league_rows: int = 0
    notes: dict = field(default_factory=dict)

    def take(self, ids: list[str]) -> np.ndarray:
        pos = {f: i for i, f in enumerate(self.fixture_ids)}
        return self.X[[pos[i] for i in ids]]


class _TeamState:
    __slots__ = ("recent", "home", "away", "kicks", "n")

    def __init__(self, maxlen: int) -> None:
        self.recent: deque = deque(maxlen=maxlen)   # (gf, ga) of the team's last matches, any venue
        self.home: deque = deque(maxlen=5)
        self.away: deque = deque(maxlen=5)
        self.kicks: list = []                       # kick-offs of its available matches (ascending)
        self.n = 0


def _mean(values) -> float:
    return float(np.mean(values)) if len(values) else math.nan


def build_features(rows: list[EvalRow], cfg: FeatureConfig) -> FeatureMatrix:
    """Features of every row in `rows`, each computed from matches available at that row's stage cutoff only."""
    schema = make_schema(cfg.windows)
    n = len(rows)
    X = np.full((n, len(schema.names)), np.nan)
    col = {nm: i for i, nm in enumerate(schema.names)}
    order = sorted(range(n), key=lambda i: (rows[i].kickoff_utc, rows[i].fixture_id))
    lag = timedelta(hours=cfg.lag_hours)
    by_avail = sorted(order, key=lambda i: (rows[i].kickoff_utc + lag, rows[i].fixture_id))
    maxlen = max(cfg.windows)
    teams: dict[str, _TeamState] = {}
    comp_recent: dict[str, deque] = {}
    comp_first: dict[tuple[str, str], object] = {}
    elo = TimeAwareElo(cfg.elo, "feat_elo", UEFA_IDS)
    diffs: dict[int, float] = {}
    uncertain: dict[int, bool] = {}
    ptr = 0
    unseen = 0
    vocab = {c: i for i, c in enumerate(cfg.vocabulary)}

    def tstate(t: str) -> _TeamState:
        s = teams.get(t)
        if s is None:
            s = teams[t] = _TeamState(maxlen)
        return s

    def apply_match(j: int) -> None:
        r = rows[j]
        gf_h, gf_a = r.home_goals or 0, r.away_goals or 0
        h, a = tstate(r.home_id), tstate(r.away_id)
        h.recent.append((gf_h, gf_a)); a.recent.append((gf_a, gf_h))  # noqa: E702
        h.home.append((gf_h, gf_a)); a.away.append((gf_a, gf_h))  # noqa: E702
        h.kicks.append(r.kickoff_utc); a.kicks.append(r.kickoff_utc)  # noqa: E702
        h.n += 1; a.n += 1  # noqa: E702
        dq = comp_recent.setdefault(r.league_id, deque(maxlen=cfg.prior_window_matches))
        dq.append((gf_h, gf_a))
        comp_first.setdefault((r.league_id, r.season), r.kickoff_utc)
        elo._update(r, diffs[j])

    for i in order:
        r = rows[i]
        cutoff = cutoff_for_stage(r.kickoff_utc, cfg.stage)
        while ptr < n and rows[by_avail[ptr]].kickoff_utc + lag <= cutoff:
            j = by_avail[ptr]
            if j not in diffs:  # a match available before this row was necessarily processed already
                raise AssertionError("availability pointer reached a match that was never processed")
            apply_match(j)
            ptr += 1
        x = X[i]
        # -- league identity
        code = vocab.get(r.league_id)
        if code is None:
            unseen += 1
            x[col["league_known"]] = 0.0
        else:
            x[col["league_code"]], x[col["league_known"]] = float(code), 1.0
        x[col["is_uefa"]] = 1.0 if r.league_id in UEFA_IDS else 0.0
        # -- league priors (last N available matches of the same competition)
        dq = comp_recent.get(r.league_id, ())
        if len(dq) >= cfg.min_prior_matches:
            hg = np.array([g[0] for g in dq]); ag = np.array([g[1] for g in dq])  # noqa: E702
            x[col["lp_goals_per_match"]] = float((hg + ag).mean())
            x[col["lp_home_win_rate"]] = float((hg > ag).mean())
            x[col["lp_draw_rate"]] = float((hg == ag).mean())
            x[col["lp_over25_rate"]] = float(((hg + ag) > 2.5).mean())
            x[col["lp_btts_rate"]] = float(((hg > 0) & (ag > 0)).mean())
            x[col["lp_home_advantage"]] = float((hg - ag).mean())
            x[col["lp_available"]] = 1.0
        else:
            x[col["lp_available"]] = 0.0
        # -- strength (Elo with the same availability rule)
        diffs[i] = elo._diff(r)
        uncertain[i] = min(elo.state.matches[r.home_id], elo.state.matches[r.away_id]) < cfg.elo.min_matches_certain
        x[col["elo_diff"]] = diffs[i]
        x[col["elo_min_matches"]] = float(min(elo.state.matches[r.home_id], elo.state.matches[r.away_id]))
        x[col["elo_certain"]] = 0.0 if uncertain[i] else 1.0
        # -- team form and venue form
        hs, as_ = tstate(r.home_id), tstate(r.away_id)
        both = min(len(hs.recent), len(as_.recent))
        form_ok = both >= cfg.min_form_matches
        x[col["form_available"]] = 1.0 if form_ok else 0.0
        for side, st in (("h", hs), ("a", as_)):
            for w in cfg.windows:
                tail = list(st.recent)[-w:]
                if len(tail) < cfg.min_form_matches:
                    continue
                x[col[f"{side}_gf{w}"]] = _mean([g[0] for g in tail])
                x[col[f"{side}_ga{w}"]] = _mean([g[1] for g in tail])
                if w == cfg.windows[0]:
                    x[col[f"{side}_pts{w}"]] = _mean([3.0 if g[0] > g[1] else (1.0 if g[0] == g[1] else 0.0) for g in tail])
        for side, dqv, venue in (("h", hs.home, "home"), ("a", as_.away, "away")):
            if len(dqv) >= cfg.min_form_matches:
                x[col[f"{side}_{venue}_gf5"]] = _mean([g[0] for g in dqv])
                x[col[f"{side}_{venue}_ga5"]] = _mean([g[1] for g in dqv])
        # -- rest and congestion
        rest_ok = bool(hs.kicks and as_.kicks)
        x[col["rest_available"]] = 1.0 if rest_ok else 0.0
        for side, st in (("h", hs), ("a", as_)):
            if st.kicks:
                x[col[f"{side}_rest_days"]] = min(cfg.rest_cap_days, (r.kickoff_utc - st.kicks[-1]).total_seconds() / 86400.0)
            lo = bisect.bisect_left(st.kicks, r.kickoff_utc - timedelta(days=cfg.congestion_days))
            x[col[f"{side}_matches_30d"]] = float(len(st.kicks) - lo)
        if rest_ok:
            x[col["rest_diff"]] = x[col["h_rest_days"]] - x[col["a_rest_days"]]
        # -- status and calendar
        x[col["h_prior_matches"]], x[col["a_prior_matches"]] = float(min(hs.n, 100)), float(min(as_.n, 100))
        x[col["either_new_team"]] = 1.0 if min(hs.n, as_.n) < cfg.new_team_matches else 0.0
        first = comp_first.get((r.league_id, r.season))
        x[col["days_into_season"]] = 0.0 if first is None else max(0.0, (r.kickoff_utc - first).total_seconds() / 86400.0)
    return FeatureMatrix(schema, X, [r.fixture_id for r in rows], unseen)


def replay_equivalence_diffs(rows: list[EvalRow], cfg: FeatureConfig) -> np.ndarray:
    """Elo differences of `build_features` (for the equivalence test against the public kick-off ordered replay)."""
    fm = build_features(rows, cfg)
    return fm.X[:, fm.schema.index("elo_diff")]


# ---------------------------------------------------------------------------------------------------- audits
def perturb(rows: list[EvalRow], from_kickoff) -> list[EvalRow]:
    """TEST/AUDIT: replace the result of every match kicking off at/after `from_kickoff` by 7-0 (a result no real match shares)."""
    return [dataclasses.replace(r, home_goals=7, away_goals=0, outcome=0) if r.kickoff_utc >= from_kickoff else r for r in rows]


def perturbation_audit(rows: list[EvalRow], cfg: FeatureConfig, points) -> dict:
    """Build the features twice, the second time with every result from `point` on replaced. Every row kicking off before
    `point` must be bit-identical (NaN equal NaN): a result from the future cannot be seen. Returns violations per point."""
    base = build_features(rows, cfg)
    out = {}
    for point in points:
        alt = build_features(perturb(rows, point), cfg)
        before = np.array([r.kickoff_utc < point for r in rows])
        same = np.array_equal(base.X[before], alt.X[before], equal_nan=True)
        changed_after = int((~np.isclose(base.X[~before], alt.X[~before], equal_nan=True)).any(axis=1).sum())
        out[point.isoformat()] = {"rows_before_point": int(before.sum()), "rows_identical": bool(same), "rows_after_point_that_changed": changed_after,
                                  "violations": 0 if same else int((~np.isclose(base.X[before], alt.X[before], equal_nan=True)).any(axis=1).sum())}  # fmt: skip
    return {"definition": "all results at/after the point are replaced by 7-0; rows before the point must not change",
            "points": out, "violations": sum(v["violations"] for v in out.values())}
