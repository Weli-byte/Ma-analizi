"""football-data.co.uk row -> canonical records. Never silently coerces missing to 0."""

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from src.schemas import Fixture, FixtureStatus

from .leagues import LEAGUES, League
from .teams import TeamRegistry

LONDON = ZoneInfo("Europe/London")  # football-data kickoff times are UK time
BOOKMAKERS = ["B365", "BW", "IW", "PS", "WH", "VC", "Avg", "Max"]
STAT_COLS = {  # canonical stat -> (home col, away col)
    "shots": ("HS", "AS"),
    "shots_on_target": ("HST", "AST"),
    "corners": ("HC", "AC"),
    "fouls": ("HF", "AF"),
    "yellow_cards": ("HY", "AY"),
    "red_cards": ("HR", "AR"),
}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return list(csv.DictReader(io.StringIO(text)))


@dataclass
class CleanRow:
    fixture: Fixture
    kickoff_time_known: bool
    stats: list[dict]  # one dict per team side
    odds: list[dict]  # one dict per (bookmaker, kind, selection)
    invalid: dict[str, int] = field(default_factory=dict)  # value problems (nulled, not zeroed)


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


def clean_row(
    raw: dict[str, str], league: League, season: str, teams: TeamRegistry
) -> CleanRow | Rejected:
    if all(_blank(v) for v in raw.values()):
        return Rejected("blank_row", "", raw)
    try:
        day = parse_date(raw.get("Date") or "")
    except ValueError as e:
        return Rejected("invalid_date", str(e), raw)

    time_known = not _blank(raw.get("Time"))
    try:
        clock = datetime.strptime(raw["Time"].strip(), "%H:%M").time() if time_known else time(0)
    except ValueError:
        time_known, clock = False, time(0)
    if time_known:
        kickoff = datetime.combine(day, clock, tzinfo=LONDON).astimezone(UTC)
    else:  # unknown time: 00:00 UTC is earlier than any real kickoff -> leakage-conservative
        kickoff = datetime.combine(day, clock, tzinfo=UTC)

    if _blank(raw.get("HomeTeam")) or _blank(raw.get("AwayTeam")):
        return Rejected("missing_team", "", raw)
    home_id = teams.resolve(raw["HomeTeam"], league.country)
    away_id = teams.resolve(raw["AwayTeam"], league.country)
    if home_id is None or away_id is None:
        return Rejected("unmatched_team", f"{raw['HomeTeam']} / {raw['AwayTeam']}", raw)

    try:
        hg, ag = _int(raw.get("FTHG")), _int(raw.get("FTAG"))
    except ValueError as e:
        return Rejected("invalid_score", str(e), raw)
    if hg is None or ag is None:
        return Rejected("missing_score", "", raw)
    ftr = (raw.get("FTR") or "").strip()
    expected = "H" if hg > ag else "A" if hg < ag else "D"
    if ftr and ftr != expected:
        return Rejected("result_mismatch", f"score {hg}-{ag} vs FTR {ftr}", raw)

    fid = f"{league.league_id}_{season}_{day:%Y%m%d}_{home_id}_{away_id}"
    try:
        fixture = Fixture(
            fixture_id=fid,
            league_id=league.league_id,
            season=season,
            kickoff_utc=kickoff,
            home_id=home_id,
            away_id=away_id,
            status=FixtureStatus.FINISHED,
            home_goals=hg,
            away_goals=ag,
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

    odds = []
    for kind, infix in (("pre_match_unspecified", ""), ("closing", "C")):
        for book in BOOKMAKERS:
            vals = [raw.get(f"{book}{infix}{s}") for s in "HDA"]
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
                odds.append(
                    {
                        "fixture_id": fid,
                        "bookmaker": book,
                        "market": "1X2",
                        "selection": sel,
                        "decimal_odds": n,
                        "snapshot_kind": kind,
                        "timestamp_utc": None,
                    }
                )
    return CleanRow(fixture, time_known, stats, odds, invalid)


def league_of(league_id: str) -> League:
    return LEAGUES[league_id]
