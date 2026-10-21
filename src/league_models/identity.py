"""Canonical identities for the league-model programme (Sprint 1, plan task 3).

A fixture is identified by `{home_team_id}__{away_team_id}__{YYYY-MM-DD}`, the key this repository already
uses for match artifacts, together with the canonical league id and the season id. Team ids come from the
existing `TeamDirectory`; a club name that does not resolve exactly is reported and the fixture is excluded,
never guessed and never auto-registered. Two clubs with the same name are never merged here (the directory
decides). The mapping is rebuilt from the cached source files, so re-running it is idempotent; there is no
fixtures database to migrate (the only database is the Turso user store), so the "migration" is this
additive mapping layer with a dry-run."""

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.data.teams import TeamDirectory
from src.ingestion import openfootball, openfootball_cups

from .competitions import Competition, season_label


@dataclass(frozen=True)
class IdentityRow:
    canonical_fixture_id: str
    provider_id: str  # key in the licence manifest
    provider_fixture_id: str  # derived by the adapter: these sources publish no match id
    canonical_league_id: str
    season_id: str
    canonical_home_team_id: str
    canonical_away_team_id: str
    kickoff_utc: datetime
    kickoff_time_known: bool  # False: only the date is published, the kick-off time is a placeholder
    status: str  # FINISHED | SCHEDULED
    final_score: tuple[int, int] | None
    stage: str | None
    source_file: str  # path relative to the repository root


@dataclass(frozen=True)
class Unresolved:
    canonical_league_id: str
    season_id: str
    side: str  # home | away
    raw_name: str
    day: str


def fixture_id(home_id: str, away_id: str, kickoff_utc: datetime) -> str:
    return f"{home_id}__{away_id}__{kickoff_utc.date().isoformat()}"


def domestic_rows(
    comp: Competition, start_year: int, raw: bytes, directory: TeamDirectory, rel_path: str
) -> tuple[list[IdentityRow], list[Unresolved]]:
    season = season_label(start_year)
    rows, bad = [], []
    for f in openfootball.parse(raw, comp.repo_key, season):
        day = f.kickoff_utc.date()
        h = directory.resolve("openfootball", f.home_team_raw_name, comp.country, day)
        a = directory.resolve("openfootball", f.away_team_raw_name, comp.country, day)
        for side, res, name in (("home", h, f.home_team_raw_name), ("away", a, f.away_team_raw_name)):
            if res.team_id is None:
                bad.append(Unresolved(comp.canonical_id, season, side, name, day.isoformat()))
        if h.team_id is None or a.team_id is None:
            continue
        finished = f.home_goals is not None and f.away_goals is not None
        rows.append(
            IdentityRow(
                fixture_id(h.team_id, a.team_id, f.kickoff_utc),
                comp.source,
                f.provider_fixture_id,
                comp.canonical_id,
                season,
                h.team_id,
                a.team_id,
                f.kickoff_utc,
                bool(f.extra.get("time_given")),
                "FINISHED" if finished else "SCHEDULED",
                (f.home_goals, f.away_goals) if finished else None,
                f.extra.get("round"),
                rel_path,
            )
        )
    return rows, bad


def cup_rows(
    comp: Competition, start_year: int, raw: bytes, directory: TeamDirectory, rel_path: str
) -> tuple[list[IdentityRow], list[Unresolved]]:
    season = season_label(start_year)
    rows, bad = [], []
    for m in openfootball_cups.parse(raw.decode("utf-8"), comp.repo_key, start_year):
        day = m.kickoff_utc.date()
        h = directory.resolve(openfootball_cups.SOURCE, m.home, m.home_cc, day)
        a = directory.resolve(openfootball_cups.SOURCE, m.away, m.away_cc, day)
        for side, res, name in (("home", h, m.home), ("away", a, m.away)):
            if res.team_id is None:
                bad.append(Unresolved(comp.canonical_id, season, side, name, day.isoformat()))
        if h.team_id is None or a.team_id is None:
            continue
        rows.append(
            IdentityRow(
                fixture_id(h.team_id, a.team_id, m.kickoff_utc),
                comp.source,
                f"{comp.repo_key}-{season}-{h.team_id}-{a.team_id}-{day.isoformat()}",
                comp.canonical_id,
                season,
                h.team_id,
                a.team_id,
                m.kickoff_utc,
                m.time_known,
                "FINISHED" if m.goals else "SCHEDULED",
                tuple(m.goals) if m.goals else None,
                m.stage,
                rel_path,
            )
        )
    return rows, bad


def collision_audit(rows: list[IdentityRow]) -> dict:
    """Identity collisions. Every list must be empty for the mapping to be trusted."""
    by_id: dict[str, list[IdentityRow]] = defaultdict(list)
    for r in rows:
        by_id[r.canonical_fixture_id].append(r)
    duplicate_ids = {k: len(v) for k, v in by_id.items() if len(v) > 1}
    cross_competition = {
        k: sorted({r.canonical_league_id for r in v})
        for k, v in by_id.items()
        if len({r.canonical_league_id for r in v}) > 1
    }
    self_matches = [
        r.canonical_fixture_id for r in rows if r.canonical_home_team_id == r.canonical_away_team_id
    ]
    per_team_day: Counter = Counter()
    for r in rows:
        day = r.kickoff_utc.date().isoformat()
        per_team_day[(r.canonical_home_team_id, day)] += 1
        per_team_day[(r.canonical_away_team_id, day)] += 1
    team_two_fixtures_same_day = sorted(f"{t}@{d}" for (t, d), n in per_team_day.items() if n > 1)
    provider_dupes = {
        k: n for k, n in Counter((r.provider_id, r.provider_fixture_id) for r in rows).items() if n > 1
    }
    return {
        "duplicate_canonical_fixture_ids": duplicate_ids,
        "ids_in_more_than_one_competition": cross_competition,
        "self_matches": self_matches,
        "team_with_two_fixtures_on_one_day": team_two_fixtures_same_day,
        "duplicate_provider_fixture_ids": {f"{p}|{i}": n for (p, i), n in provider_dupes.items()},
    }


def mapping_lines(rows: list[IdentityRow]) -> list[str]:
    """Deterministic JSON lines of the mapping, sorted by (league, season, kickoff, id)."""
    ordered = sorted(
        rows, key=lambda r: (r.canonical_league_id, r.season_id, r.kickoff_utc, r.canonical_fixture_id)
    )
    out = []
    for r in ordered:
        d = asdict(r)
        d["kickoff_utc"] = r.kickoff_utc.isoformat()
        d["final_score"] = list(r.final_score) if r.final_score else None
        out.append(json.dumps(d, sort_keys=True, ensure_ascii=False))
    return out


def mapping_hash(rows: list[IdentityRow]) -> str:
    return hashlib.sha256("\n".join(mapping_lines(rows)).encode("utf-8")).hexdigest()


def upsert_mapping(path: Path, rows: list[IdentityRow], apply: bool = False) -> dict:
    """Idempotent upsert of the mapping file keyed by (canonical league, canonical fixture id). Dry-run unless
    `apply`. Returns counts of added, changed, removed and unchanged rows; writing the same rows twice
    changes nothing."""
    new = {
        (json.loads(x)["canonical_league_id"], json.loads(x)["canonical_fixture_id"]): x
        for x in mapping_lines(rows)
    }
    old: dict[tuple[str, str], str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            old[(d["canonical_league_id"], d["canonical_fixture_id"])] = line
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k] != old[k]]
    result = {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "unchanged": len(new) - len(added) - len(changed),
        "applied": apply,
    }
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(mapping_lines(rows)) + "\n", encoding="utf-8")
    return result
