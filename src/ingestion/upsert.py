"""S12: idempotent fixture upsert -- `RawFixture` (provider-native) -> `schemas.Fixture`
(canonical team ids, validated status), with team identity resolved through the EXISTING
`src.data.teams.TeamDirectory` (S0-S3) rather than a parallel resolver.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from src.data.teams import TeamDirectory
from src.schemas import FixtureStatus
from src.schemas.fixture import Fixture

from .provider import RawFixture

# Generic default mapping (API-Football-style vocabulary is the most common shape in this
# space) -- callers pass their own `status_map` for a provider with different strings; this is
# a DEFAULT, never a hardcoded assumption about which commercial vendor is in use.
DEFAULT_STATUS_MAP: dict[str, FixtureStatus] = {
    "NS": FixtureStatus.SCHEDULED, "TBD": FixtureStatus.SCHEDULED,
    "1H": FixtureStatus.IN_PROGRESS, "HT": FixtureStatus.IN_PROGRESS, "2H": FixtureStatus.IN_PROGRESS,
    "ET": FixtureStatus.IN_PROGRESS, "P": FixtureStatus.IN_PROGRESS, "LIVE": FixtureStatus.IN_PROGRESS,
    "FT": FixtureStatus.FINISHED, "AET": FixtureStatus.FINISHED, "PEN": FixtureStatus.FINISHED,
    "PST": FixtureStatus.POSTPONED,
    "CANC": FixtureStatus.CANCELLED,
    "ABD": FixtureStatus.ABANDONED, "AWD": FixtureStatus.ABANDONED, "WO": FixtureStatus.ABANDONED,
}  # fmt: skip


class UnknownStatusError(ValueError):
    """A provider status string with no entry in `status_map` -- refused, not guessed."""


@dataclass(frozen=True)
class UpsertResult:
    fixture: Fixture | None  # None iff team_resolution_pending is non-empty
    is_new: bool
    changed: bool
    team_resolution_pending: tuple[str, ...] = ()  # raw names TeamDirectory couldn't resolve


def resolve_status(status_raw: str, status_map: dict[str, FixtureStatus] | None = None) -> FixtureStatus:
    mapping = status_map or DEFAULT_STATUS_MAP
    if status_raw not in mapping:
        raise UnknownStatusError(f"unknown provider status {status_raw!r}; extend status_map")
    return mapping[status_raw]


def build_fixture(
    raw: RawFixture,
    directory: TeamDirectory,
    source: str,
    country: str,
    status_map: dict[str, FixtureStatus] | None = None,
    ingested_at: datetime | None = None,
) -> tuple[Fixture | None, tuple[str, ...]]:
    """Returns `(fixture, pending)`. `fixture` is `None` if either team could not be resolved
    (`pending` names them) -- NEVER auto-registered here (ADR 0010's safety-first default:
    unknown team names go to the review queue, not a guessed mapping)."""
    status = resolve_status(raw.status_raw, status_map)
    home = directory.resolve(source, raw.home_team_raw_name, country, raw.kickoff_utc.date())
    away = directory.resolve(source, raw.away_team_raw_name, country, raw.kickoff_utc.date())
    pending = tuple(
        n for n, r in ((raw.home_team_raw_name, home), (raw.away_team_raw_name, away)) if r.team_id is None
    )
    if pending:
        return None, pending

    kwargs = {
        "fixture_id": raw.provider_fixture_id,
        "league_id": raw.league_id,
        "season": raw.season,
        "kickoff_utc": raw.kickoff_utc,
        "home_id": home.team_id,
        "away_id": away.team_id,
        "status": status,
    }
    if status == FixtureStatus.FINISHED:
        if raw.home_goals is None or raw.away_goals is None:
            raise ValueError(f"{raw.provider_fixture_id}: FINISHED without a score")
        # We only genuinely KNOW the result at the moment this sync observed it -- never
        # backdated to kickoff or invented, matching this repo's leakage-honesty rule for any
        # OTHER (non-historical-CSV) source (ADR 0006/0008's "inferred" label is for the CSV
        # pipeline specifically, where no publication time exists at all; here we have a real
        # observation time, so it is "observed", not "inferred").
        kwargs["home_goals"] = raw.home_goals
        kwargs["away_goals"] = raw.away_goals
        kwargs["finished_at_utc"] = ingested_at or datetime.now(UTC)
        kwargs["result_available_at_utc"] = ingested_at or datetime.now(UTC)
        kwargs["result_available_at_source"] = "observed"
    fixture = Fixture.model_validate(kwargs)
    return fixture, ()


def upsert_fixture(
    existing: Fixture | None,
    raw: RawFixture,
    directory: TeamDirectory,
    source: str,
    country: str,
    status_map: dict[str, FixtureStatus] | None = None,
    ingested_at: datetime | None = None,
) -> UpsertResult:
    """`.fixture is None` iff team identity could not be resolved for either side -- caller
    should route the raw names to `team_resolution review` (ADR 0010), not drop the fixture
    silently."""
    fixture, pending = build_fixture(raw, directory, source, country, status_map, ingested_at)
    if fixture is None:
        return UpsertResult(fixture=None, is_new=False, changed=False, team_resolution_pending=pending)
    if existing is None:
        return UpsertResult(fixture, is_new=True, changed=True)
    changed = existing.model_dump(exclude={"status"}) != fixture.model_dump(exclude={"status"}) or (
        existing.status != fixture.status
    )
    return UpsertResult(fixture, is_new=False, changed=changed)
