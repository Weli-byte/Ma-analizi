"""S12 (ADR 0023): global fixture ingestion -- provider adapter interface, rate-limit/backoff,
cache/audit storage, idempotent upsert, coverage matrix, orchestration. Every test here uses a
MOCK provider (no network) -- the sprint's own instruction ("Mock API integration tests yaz")."""

from datetime import UTC, date, datetime, timedelta

import pytest

from src.data.teams import Alias, TeamDirectory
from src.ingestion.cache import ResponseCache
from src.ingestion.coverage import CoverageMatrix
from src.ingestion.provider import (
    FixtureProvider,
    League,
    ProviderError,
    RateLimitedError,
    RawFixture,
    Season,
)
from src.ingestion.rate_limit import RateLimiter, with_backoff
from src.ingestion.sync import sync_league_season
from src.ingestion.upsert import UnknownStatusError, build_fixture, resolve_status, upsert_fixture
from src.schemas import FixtureStatus

T0 = datetime(2024, 3, 1, 15, 0, tzinfo=UTC)


def directory():
    d = TeamDirectory()
    d.teams = {
        "ENG_alpha": {"team_id": "ENG_alpha", "canonical_name": "Alpha FC", "country": "ENG"},
        "ENG_beta": {"team_id": "ENG_beta", "canonical_name": "Beta United", "country": "ENG"},
    }
    d.aliases = [
        Alias("mock-provider", "Alpha FC", "ENG_alpha"),
        Alias("mock-provider", "Beta United", "ENG_beta"),
    ]
    return d


def raw_fixture(fid="f1", status="NS", home="Alpha FC", away="Beta United", goals=None):
    hg, ag = goals or (None, None)
    return RawFixture(fid, "EPL", "2023-24", T0, home, away, status, home_goals=hg, away_goals=ag)


# -------------------------------------------------------------------------------- provider
class MockProvider(FixtureProvider):
    name = "mock-provider"

    def __init__(self, fixtures: list[RawFixture] | None = None, fail: bool = False):
        self._fixtures = fixtures or [raw_fixture()]
        self.fail = fail
        self.calls = 0

    def list_leagues(self) -> list[League]:
        return [League("EPL", "Premier League", "ENG")]

    def list_seasons(self, league_id: str) -> list[Season]:
        return [Season(league_id, "2023-24", date(2023, 8, 1), date(2024, 5, 31))]

    def list_fixtures(self, league_id: str, season: str) -> list[RawFixture]:
        self.calls += 1
        if self.fail:
            raise ProviderError("boom")
        return self._fixtures


def test_mock_provider_satisfies_the_protocol():
    p: FixtureProvider = MockProvider()
    assert p.list_leagues()[0].league_id == "EPL"
    assert p.list_seasons("EPL")[0].season == "2023-24"
    assert len(p.list_fixtures("EPL", "2023-24")) == 1


def test_provider_optional_endpoints_raise_not_implemented_not_empty():
    """An unsupported endpoint must raise, never silently return []  -- empty means "provider
    legitimately has nothing," not "we didn't build this yet"."""
    p = MockProvider()
    with pytest.raises(NotImplementedError):
        p.list_lineups("f1")
    with pytest.raises(NotImplementedError):
        p.list_odds("f1")


# -------------------------------------------------------------------------------- rate_limit
def test_rate_limiter_does_not_sleep_when_under_the_limit():
    sleeps = []
    rl = RateLimiter(max_calls=5, period_seconds=1.0, sleep_fn=sleeps.append)
    for _ in range(5):
        rl.acquire()
    assert sleeps == []


def test_rate_limiter_sleeps_once_the_bucket_is_exhausted():
    sleeps = []
    rl = RateLimiter(max_calls=2, period_seconds=10.0, sleep_fn=sleeps.append)
    rl.acquire()
    rl.acquire()
    rl.acquire()  # third call within the window must wait
    assert len(sleeps) == 1 and sleeps[0] > 0


def test_rate_limiter_rejects_non_positive_arguments():
    with pytest.raises(ValueError):
        RateLimiter(0, 1.0)
    with pytest.raises(ValueError):
        RateLimiter(1, 0.0)


def test_with_backoff_retries_rate_limited_errors_and_eventually_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitedError("429")
        return "ok"

    sleeps = []
    assert with_backoff(flaky, max_retries=5, base_delay=0.01, sleep_fn=sleeps.append) == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential


def test_with_backoff_gives_up_after_max_retries():
    def always_limited():
        raise RateLimitedError("429")

    with pytest.raises(RateLimitedError):
        with_backoff(always_limited, max_retries=2, base_delay=0.001, sleep_fn=lambda s: None)


def test_with_backoff_does_not_retry_a_plain_provider_error():
    calls = {"n": 0}

    def auth_failure():
        calls["n"] += 1
        raise ProviderError("401 unauthorized")

    with pytest.raises(ProviderError):
        with_backoff(auth_failure, max_retries=5, sleep_fn=lambda s: None)
    assert calls["n"] == 1  # never retried


# -------------------------------------------------------------------------------------- cache
def test_cache_put_then_get_round_trips(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("k1", {"a": 1})
    entry = cache.get("k1")
    assert entry is not None and entry.payload == {"a": 1}


def test_cache_miss_returns_none(tmp_path):
    assert ResponseCache(tmp_path).get("nope") is None


def test_cache_respects_ttl(tmp_path):
    clock = {"t": 0.0}
    cache = ResponseCache(tmp_path, ttl_seconds=10, time_fn=lambda: clock["t"])
    cache.put("k1", {"a": 1})
    clock["t"] = 5
    assert cache.get("k1") is not None
    clock["t"] = 11
    assert cache.get("k1") is None  # stale


def test_cache_entry_persists_as_an_audit_record_on_disk(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("k1", {"a": 1})
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    import json

    on_disk = json.loads(files[0].read_text())
    assert on_disk["payload"] == {"a": 1} and "content_hash" in on_disk and "fetched_at_unix" in on_disk


def test_get_or_fetch_only_calls_fetch_fn_once(tmp_path):
    cache = ResponseCache(tmp_path)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"v": calls["n"]}

    first = cache.get_or_fetch("k", fetch)
    second = cache.get_or_fetch("k", fetch)
    assert first.payload == second.payload == {"v": 1}
    assert calls["n"] == 1


# -------------------------------------------------------------------------------------- upsert
def test_resolve_status_maps_known_codes():
    assert resolve_status("FT") == FixtureStatus.FINISHED
    assert resolve_status("NS") == FixtureStatus.SCHEDULED


def test_resolve_status_rejects_unknown_codes():
    with pytest.raises(UnknownStatusError):
        resolve_status("SOME_NEW_VENDOR_CODE")


def test_build_fixture_resolves_team_identity_and_validates_schema():
    fixture, pending = build_fixture(raw_fixture(), directory(), "mock-provider", "ENG")
    assert pending == ()
    assert fixture.home_id == "ENG_alpha" and fixture.away_id == "ENG_beta"
    assert fixture.status == FixtureStatus.SCHEDULED


def test_build_fixture_returns_pending_for_unresolved_team_names():
    raw = raw_fixture(home="Totally Unknown FC")
    fixture, pending = build_fixture(raw, directory(), "mock-provider", "ENG")
    assert fixture is None
    assert "Totally Unknown FC" in pending


def test_build_fixture_never_auto_registers_an_unresolved_team():
    """ADR 0010's safety-first default -- ingestion must not silently grow the team directory."""
    d = directory()
    before = dict(d.teams)
    build_fixture(raw_fixture(home="Mystery FC"), d, "mock-provider", "ENG")
    assert d.teams == before


def test_build_fixture_finished_sets_observed_result_availability_at_ingestion_time():
    raw = raw_fixture(status="FT", goals=(2, 1))
    ingested = T0 + timedelta(hours=3)
    fixture, _ = build_fixture(raw, directory(), "mock-provider", "ENG", ingested_at=ingested)
    assert fixture.home_goals == 2 and fixture.away_goals == 1
    assert fixture.result_available_at_source == "observed"
    assert fixture.result_available_at_utc == ingested


def test_build_fixture_finished_without_a_score_raises():
    raw = raw_fixture(status="FT")  # no goals
    with pytest.raises(ValueError):
        build_fixture(raw, directory(), "mock-provider", "ENG")


def test_upsert_fixture_is_new_on_first_sight():
    result = upsert_fixture(None, raw_fixture(), directory(), "mock-provider", "ENG")
    assert result.is_new and result.changed


def test_upsert_fixture_is_unchanged_when_content_is_identical():
    first = upsert_fixture(None, raw_fixture(), directory(), "mock-provider", "ENG")
    second = upsert_fixture(first.fixture, raw_fixture(), directory(), "mock-provider", "ENG")
    assert not second.is_new and not second.changed


def test_upsert_fixture_detects_a_status_change():
    first = upsert_fixture(None, raw_fixture(status="NS"), directory(), "mock-provider", "ENG")
    second = upsert_fixture(
        first.fixture, raw_fixture(status="FT", goals=(1, 0)), directory(), "mock-provider", "ENG"
    )
    assert not second.is_new and second.changed


def test_upsert_fixture_is_idempotent_across_many_identical_calls():
    result = None
    for _ in range(5):
        result = upsert_fixture(result.fixture if result else None, raw_fixture(), directory(), "mock-provider", "ENG")
    assert result.fixture.fixture_id == "f1"


# ----------------------------------------------------------------------------------- coverage
def test_coverage_matrix_records_success_and_lists_cells():
    m = CoverageMatrix()
    m.record_success("EPL", "2023-24", "fixtures", at=T0)
    cells = m.cells()
    assert len(cells) == 1 and cells[0].last_success_utc == T0.isoformat()


def test_coverage_matrix_error_does_not_erase_a_prior_success():
    m = CoverageMatrix()
    m.record_success("EPL", "2023-24", "fixtures", at=T0)
    m.record_error("EPL", "2023-24", "fixtures", "timeout")
    cell = m.cells()[0]
    assert cell.last_success_utc == T0.isoformat() and cell.last_error == "timeout"


def test_coverage_matrix_stale_cells_flags_old_and_never_succeeded():
    m = CoverageMatrix()
    m.record_success("EPL", "2023-24", "fixtures", at=T0)
    m.record_error("LALIGA", "2023-24", "fixtures", "boom")  # never succeeded
    stale = m.stale_cells(max_age_hours=1, now=T0 + timedelta(hours=5))
    leagues = {c.league_id for c in stale}
    assert leagues == {"EPL", "LALIGA"}


def test_coverage_matrix_json_round_trip(tmp_path):
    m = CoverageMatrix()
    m.record_success("EPL", "2023-24", "fixtures", at=T0)
    path = tmp_path / "coverage.json"
    m.dump(path)
    loaded = CoverageMatrix.load(path)
    assert loaded.cells() == m.cells()


def test_coverage_matrix_load_missing_file_is_empty(tmp_path):
    assert CoverageMatrix.load(tmp_path / "nope.json").cells() == []


# -------------------------------------------------------------------------------------- sync
def test_sync_league_season_happy_path():
    provider = MockProvider([raw_fixture()])
    coverage = CoverageMatrix()
    result, upserts = sync_league_season(
        provider, "EPL", "2023-24", directory(), "ENG", coverage=coverage, ingested_at=T0
    )
    assert result.fetched == 1 and result.upserted == 1 and result.unchanged == 0
    assert result.error is None
    assert upserts[0].fixture.fixture_id == "f1"
    assert coverage.cells()[0].last_success_utc == T0.isoformat()


def test_sync_league_season_second_run_is_unchanged():
    provider = MockProvider([raw_fixture()])
    r1, upserts1 = sync_league_season(provider, "EPL", "2023-24", directory(), "ENG", ingested_at=T0)
    existing = {u.fixture.fixture_id: u.fixture for u in upserts1}
    r2, _ = sync_league_season(
        provider, "EPL", "2023-24", directory(), "ENG", existing_by_fixture_id=existing, ingested_at=T0
    )
    assert r2.upserted == 0 and r2.unchanged == 1


def test_sync_league_season_records_pending_team_resolution():
    provider = MockProvider([raw_fixture(home="Totally Unknown FC")])
    result, upserts = sync_league_season(provider, "EPL", "2023-24", directory(), "ENG")
    assert "Totally Unknown FC" in result.pending_team_resolution
    assert upserts[0].fixture is None


def test_sync_league_season_provider_error_records_coverage_error_not_a_crash():
    provider = MockProvider(fail=True)
    coverage = CoverageMatrix()
    result, upserts = sync_league_season(provider, "EPL", "2023-24", directory(), "ENG", coverage=coverage)
    assert result.error is not None and result.fetched == 0
    assert upserts == []
    assert coverage.cells()[0].last_error is not None


def test_sync_league_season_uses_the_rate_limiter():
    provider = MockProvider()
    sleeps = []
    rl = RateLimiter(max_calls=1, period_seconds=100.0, sleep_fn=sleeps.append)
    rl.acquire()  # exhaust the bucket up front
    sync_league_season(provider, "EPL", "2023-24", directory(), "ENG", rate_limiter=rl)
    assert len(sleeps) == 1


def test_sync_league_season_uses_the_cache_on_a_second_call(tmp_path):
    provider = MockProvider([raw_fixture()])
    cache = ResponseCache(tmp_path)
    sync_league_season(provider, "EPL", "2023-24", directory(), "ENG", cache=cache, ingested_at=T0)
    sync_league_season(provider, "EPL", "2023-24", directory(), "ENG", cache=cache, ingested_at=T0)
    assert provider.calls == 1  # second sync served entirely from cache
