"""football-data row -> canonical records. Never silently coerces missing values to 0."""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from src.config import LeagueFormat, TeamResolutionConfig
from src.schemas import Fixture, FixtureStatus

from .raw_validation import decode
from .teams import TeamDirectory
from .timezones import InvalidLocalTime, date_only_to_utc, local_to_utc

SOURCE_NAME = "football-data"
BOOKMAKERS = ["B365", "BW", "IW", "PS", "WH", "VC"]  # real bookmakers
AGGREGATES = {"Avg": "avg", "Max": "max"}  # market aggregates, NOT bookmakers
STAT_COLS = {  # canonical stat -> (home col, away col)
    "shots": ("HS", "AS"),
    "shots_on_target": ("HST", "AST"),
    "corners": ("HC", "AC"),
    "fouls": ("HF", "AF"),
    "yellow_cards": ("HY", "AY"),
    "red_cards": ("HR", "AR"),
}
# football-data gives no snapshot time: pre-match odds were collected at an unspecified time
# before the match, closing odds shortly before kickoff. Neither is time-aligned.
SNAPSHOT_KINDS = (("pre_match", ""), ("closing", "C"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    text, _ = decode(path.read_bytes())
    return list(csv.DictReader(io.StringIO(text)))


@dataclass
class CleanRow:
    fixture: Fixture
    kickoff_time_known: bool
    stats: list[dict]  # one dict per team side
    odds: list[dict]  # one dict per (source, snapshot, selection)
    invalid: dict[str, int] = field(default_factory=dict)  # value problems (nulled, not zeroed)
    goals: tuple[int, int] | None = None
    match_day: date | None = None


@dataclass
class Rejected:
    reason: str
    detail: str
    raw: dict[str, str]


def _blank(v: str | None) -> bool:
    return v is None or v.strip() == ""


def parse_date(s: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {s!r}")


def _int(v: str | None) -> int | None:
    if _blank(v):
        return None
    f = float(v)  # type: ignore[arg-type]
    if f != int(f) or f < 0:
        raise ValueError(f"not a non-negative integer: {v!r}")
    return int(f)


def _odds_rows(raw: dict[str, str], fid: str, invalid: dict[str, int]) -> list[dict]:
    rows: list[dict] = []
    sources = [(b, "bookmaker", None, b) for b in BOOKMAKERS] + [
        (col, "aggregate", kind, None) for col, kind in AGGREGATES.items()
    ]
    for snapshot, infix in SNAPSHOT_KINDS:
        for col, mtype, akind, bookmaker in sources:
            vals = [raw.get(f"{col}{infix}{s}") for s in "HDA"]
            if all(_blank(v) for v in vals):
                continue
            try:
                nums = [float(v) for v in vals]  # type: ignore[arg-type]
            except (TypeError, ValueError):
                invalid["odds_incomplete"] = invalid.get("odds_incomplete", 0) + 1
                continue
            if any(n <= 1.0 for n in nums):
                invalid["odds_le_1"] = invalid.get("odds_le_1", 0) + 1
                continue
            for sel, n in zip("HDA", nums, strict=True):
                rows.append(
                    {
                        "fixture_id": fid,
                        "source": SOURCE_NAME,
                        "bookmaker": bookmaker,
                        "market_source_type": mtype,
                        "aggregate_kind": akind,
                        "market": "1X2",
                        "selection": sel,
                        "price": n,
                        "snapshot_type": snapshot,
                        "timestamp_utc": None,
                        "timestamp_quality": "unknown",
                    }
                )
    return rows


def clean_row(
    raw: dict[str, str],
    league_id: str,
    league: LeagueFormat,
    season: str,
    teams: TeamDirectory,
    team_cfg: TeamResolutionConfig,
    result_lag: timedelta,
    as_of: date,
) -> CleanRow | Rejected:
    if all(_blank(v) for v in raw.values()):
        return Rejected("blank_row", "", raw)
    try:
        day = parse_date(raw.get("Date") or "")
    except ValueError as e:
        return Rejected("invalid_date", str(e), raw)

    time_known = not _blank(raw.get("Time"))
    if time_known:
        try:
            clock = datetime.strptime(raw["Time"].strip(), "%H:%M").time()
            kickoff = local_to_utc(day, clock, league.source_timezone)
        except (ValueError, InvalidLocalTime) as e:
            return Rejected("invalid_local_time", str(e), raw)
    else:
        kickoff = date_only_to_utc(day)

    if _blank(raw.get("HomeTeam")) or _blank(raw.get("AwayTeam")):
        return Rejected("missing_team", "", raw)
    ids = []
    for col in ("HomeTeam", "AwayTeam"):
        res = teams.resolve(
            SOURCE_NAME,
            raw[col],
            league.country,
            day,
            team_cfg.suggest_cutoff,
            team_cfg.auto_register_new_teams,
        )
        ids.append(res.team_id)
    home_id, away_id = ids
    if home_id is None or away_id is None:
        return Rejected("unmatched_team", f"{raw['HomeTeam']} / {raw['AwayTeam']}", raw)

    try:
        hg, ag = _int(raw.get("FTHG")), _int(raw.get("FTAG"))
    except ValueError as e:
        return Rejected("invalid_score", str(e), raw)

    fid = f"{league_id}_{season}_{day:%Y%m%d}_{home_id}_{away_id}"
    if hg is None or ag is None:
        if day > as_of:  # not played yet: a future fixture, not a data error
            try:
                fixture = Fixture(
                    fixture_id=fid,
                    league_id=league_id,
                    season=season,
                    kickoff_utc=kickoff,
                    home_id=home_id,
                    away_id=away_id,
                    status=FixtureStatus.SCHEDULED,
                )
            except ValueError as e:
                return Rejected("schema_violation", str(e).splitlines()[0], raw)
            return CleanRow(fixture, time_known, [], [], {}, None, day)
        return Rejected("missing_score", "", raw)

    ftr = (raw.get("FTR") or "").strip()
    expected = "H" if hg > ag else "A" if hg < ag else "D"
    if ftr and ftr != expected:
        return Rejected("result_mismatch", f"score {hg}-{ag} vs FTR {ftr}", raw)

    try:
        fixture = Fixture(
            fixture_id=fid,
            league_id=league_id,
            season=season,
            kickoff_utc=kickoff,
            home_id=home_id,
            away_id=away_id,
            status=FixtureStatus.FINISHED,
            home_goals=hg,
            away_goals=ag,
            result_available_at_utc=kickoff + result_lag,
            result_available_at_source="inferred",  # source has no publication time
        )
    except ValueError as e:
        return Rejected("schema_violation", str(e).splitlines()[0], raw)

    invalid: dict[str, int] = {}
    stats = []
    for side, team_id, idx in (("home", home_id, 0), ("away", away_id, 1)):
        row: dict = {"fixture_id": fid, "team_id": team_id, "side": side}
        for stat, cols in STAT_COLS.items():
            try:
                row[stat] = _int(raw.get(cols[idx]))
            except ValueError:
                row[stat] = None
                invalid[f"stat_{stat}"] = invalid.get(f"stat_{stat}", 0) + 1
        stats.append(row)
    return CleanRow(fixture, time_known, stats, _odds_rows(raw, fid, invalid), invalid, (hg, ag), day)
