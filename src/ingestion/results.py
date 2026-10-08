"""Recent finished results -> match history (ADR 0032).

The football-data.co.uk dataset is the research history, but its source may be unreachable (it was on
2026-10-06) and its newest result was 2026-08-27. For LIVE forecasting that gap would silently weaken
every form feature, so finished results are also ingested from football-data.org into an append-only
store and merged into the `MatchHistory` used by the snapshot stages.

Leakage rules: a result becomes usable only from the moment WE first observed it
(`result_available_at_utc = first_seen`, source "observed", never backdated to kickoff), exactly like
S12 upserts. Team names resolve through `TeamDirectory` (unresolved -> skipped and counted, never
auto-registered). A match already in the dataset (same teams, same kickoff day) is not added twice.
The research dataset, its checksums and `data_version` are untouched.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from src.data.teams import TeamDirectory
from src.features.history import MatchRecord
from src.schemas import FixtureStatus

from .provider import RawFixture

STORE_NAME = "ingested_matches.jsonl"
SOURCE = "football-data-org"


def store_path(root: Path) -> Path:
    d = Path(root) / "artifacts" / "ingestion"
    d.mkdir(parents=True, exist_ok=True)
    return d / STORE_NAME


def _key(home_id: str, away_id: str, kickoff: datetime) -> tuple[str, str, str]:
    return (home_id, away_id, kickoff.astimezone(UTC).date().isoformat())


def read_store(root: Path) -> list[dict]:
    p = store_path(root)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def ingest_finished(
    root: Path, raws: list[RawFixture], directory: TeamDirectory, country: str, repo_league: str,
    now: datetime | None = None, source: str = SOURCE, id_prefix: str = "fdorg",
) -> dict:  # fmt: skip
    """Append finished results not stored yet. Returns counts (new / known / unresolved / not_finished)."""
    now = now or datetime.now(UTC)
    known = {
        _key(r["home_id"], r["away_id"], datetime.fromisoformat(r["kickoff_utc"])) for r in read_store(root)
    }
    counts = {"new": 0, "known": 0, "unresolved": 0, "not_finished": 0}
    with store_path(root).open("a", encoding="utf-8") as f:
        for raw in raws:
            if raw.status_raw not in ("FT", "AWD") or raw.home_goals is None or raw.away_goals is None:
                counts["not_finished"] += 1
                continue
            h = directory.resolve(source, raw.home_team_raw_name, country, raw.kickoff_utc.date())
            a = directory.resolve(source, raw.away_team_raw_name, country, raw.kickoff_utc.date())
            if h.team_id is None or a.team_id is None:
                counts["unresolved"] += 1
                continue
            k = _key(h.team_id, a.team_id, raw.kickoff_utc)
            if k in known:
                counts["known"] += 1
                continue
            known.add(k)
            f.write(
                json.dumps(
                    {
                        "fixture_id": f"{id_prefix}-{raw.provider_fixture_id}", "league": repo_league,
                        "season": raw.season, "kickoff_utc": raw.kickoff_utc.isoformat(),
                        "home_id": h.team_id, "away_id": a.team_id,
                        "home_goals": raw.home_goals, "away_goals": raw.away_goals,
                        "first_seen_utc": now.isoformat(), "source": source,
                    },
                    sort_keys=True,
                )
                + "\n"
            )  # fmt: skip
            counts["new"] += 1
    return counts


def ingested_matches(root: Path) -> list[MatchRecord]:
    out = []
    for r in read_store(root):
        out.append(
            MatchRecord(
                fixture_id=r["fixture_id"],
                season=r["season"],
                kickoff_utc=datetime.fromisoformat(r["kickoff_utc"]),
                home_id=r["home_id"],
                away_id=r["away_id"],
                home_goals=r["home_goals"],
                away_goals=r["away_goals"],
                result_available_at_utc=datetime.fromisoformat(r["first_seen_utc"]),
                status=FixtureStatus.FINISHED,
            )  # fmt: skip
        )
    return out


def merge_history(dataset: list[MatchRecord], ingested: list[MatchRecord]) -> list[MatchRecord]:
    """Dataset matches first; an ingested match is added only if the dataset lacks that fixture."""
    have = {_key(m.home_id, m.away_id, m.kickoff_utc) for m in dataset}
    extra = [m for m in ingested if _key(m.home_id, m.away_id, m.kickoff_utc) not in have]
    return sorted([*dataset, *extra], key=lambda m: (m.kickoff_utc, m.fixture_id))
