"""S12: the real football-data.org adapter -- every network call is mocked (`_get`), same seam
as `src/llm/providers.py::_post_json`. No network access or API key needed to run these tests.
"""

import pytest

from src.config import IngestionConfig
from src.ingestion.football_data_org import STATUS_MAP, FootballDataOrgProvider
from src.ingestion.provider import League, ProviderError, RateLimitedError
from src.ingestion.sync import IngestionProviderNotConfigured, resolve_ingestion_provider


def test_list_leagues_parses_competitions(monkeypatch):
    def fake_get(path, api_key, params=None, timeout=15.0):
        assert path == "/competitions"
        return {"competitions": [{"id": 2021, "name": "Premier League", "area": {"code": "ENG", "name": "England"}}]}

    monkeypatch.setattr("src.ingestion.football_data_org._get", fake_get)
    leagues = FootballDataOrgProvider("key").list_leagues()
    assert leagues == [League("2021", "Premier League", "ENG")]


def test_list_seasons_parses_season_dates(monkeypatch):
    def fake_get(path, api_key, params=None, timeout=15.0):
        assert path == "/competitions/2021"
        return {"seasons": [{"id": 733, "startDate": "2023-08-11", "endDate": "2024-05-19"}]}

    monkeypatch.setattr("src.ingestion.football_data_org._get", fake_get)
    seasons = FootballDataOrgProvider("key").list_seasons("2021")
    assert len(seasons) == 1 and seasons[0].season == "2023-24"


def test_list_fixtures_maps_status_and_score(monkeypatch):
    def fake_get(path, api_key, params=None, timeout=15.0):
        assert path == "/competitions/2021/matches"
        assert params == {"season": 2023}
        return {
            "matches": [
                {
                    "id": 12345, "utcDate": "2024-03-01T15:00:00Z", "status": "FINISHED",
                    "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Chelsea FC"},
                    "score": {"fullTime": {"home": 2, "away": 1}},
                }
            ]
        }  # fmt: skip

    monkeypatch.setattr("src.ingestion.football_data_org._get", fake_get)
    fixtures = FootballDataOrgProvider("key").list_fixtures("2021", "2023-24")
    assert len(fixtures) == 1
    f = fixtures[0]
    assert f.provider_fixture_id == "12345"
    assert f.status_raw == "FT"
    assert f.home_goals == 2 and f.away_goals == 1
    assert f.home_team_raw_name == "Arsenal FC"
    assert f.kickoff_utc.isoformat() == "2024-03-01T15:00:00+00:00"


def test_list_fixtures_handles_no_score_yet(monkeypatch):
    def fake_get(path, api_key, params=None, timeout=15.0):
        return {
            "matches": [
                {
                    "id": 1, "utcDate": "2024-03-01T15:00:00Z", "status": "SCHEDULED",
                    "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"}, "score": {"fullTime": {}},
                }
            ]
        }  # fmt: skip

    monkeypatch.setattr("src.ingestion.football_data_org._get", fake_get)
    f = FootballDataOrgProvider("key").list_fixtures("2021", "2023-24")[0]
    assert f.status_raw == "NS" and f.home_goals is None and f.away_goals is None


def test_optional_endpoints_still_raise_not_implemented():
    p = FootballDataOrgProvider("key")
    with pytest.raises(NotImplementedError):
        p.list_lineups("1")


def test_status_map_covers_every_documented_v4_status():
    documented = {
        "SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "FINISHED",
        "SUSPENDED", "POSTPONED", "CANCELLED", "AWARDED",
    }  # fmt: skip
    assert documented <= set(STATUS_MAP)


def test_get_raises_rate_limited_on_429(monkeypatch):
    import urllib.error

    from src.ingestion.football_data_org import _get

    def fake_urlopen(req, timeout=15.0):
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RateLimitedError):
        _get("/competitions", "key")


def test_get_raises_provider_error_on_other_http_errors(monkeypatch):
    import urllib.error

    from src.ingestion.football_data_org import _get

    def fake_urlopen(req, timeout=15.0):
        raise urllib.error.HTTPError("url", 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ProviderError):
        _get("/competitions", "key")


# ------------------------------------------------------------- resolve_ingestion_provider
def test_resolve_ingestion_provider_reads_ingestion_yaml(monkeypatch):
    monkeypatch.setenv("TEST_FD_KEY", "real-key")
    cfg = IngestionConfig(
        providers={
            "football-data-org": {
                "enabled": True, "api_key_env": "TEST_FD_KEY", "leagues": ["2021", "2014"],
            }
        }
    )  # fmt: skip
    provider, api_key, leagues = resolve_ingestion_provider(cfg, "football-data-org")
    assert isinstance(provider, FootballDataOrgProvider)
    assert api_key == "real-key"
    assert leagues == ["2021", "2014"]


def test_resolve_ingestion_provider_rejects_disabled_and_missing_key(monkeypatch):
    disabled = IngestionConfig(
        providers={"football-data-org": {"enabled": False, "api_key_env": "X"}}
    )
    with pytest.raises(IngestionProviderNotConfigured):
        resolve_ingestion_provider(disabled, "football-data-org")

    monkeypatch.delenv("UNSET_FD_KEY", raising=False)
    enabled_no_key = IngestionConfig(
        providers={"football-data-org": {"enabled": True, "api_key_env": "UNSET_FD_KEY"}}
    )
    with pytest.raises(IngestionProviderNotConfigured):
        resolve_ingestion_provider(enabled_no_key, "football-data-org")


def test_resolve_ingestion_provider_rejects_unregistered_adapter(monkeypatch):
    monkeypatch.setenv("TEST_X_KEY", "k")
    cfg = IngestionConfig(providers={"some-other-vendor": {"enabled": True, "api_key_env": "TEST_X_KEY"}})
    with pytest.raises(IngestionProviderNotConfigured):
        resolve_ingestion_provider(cfg, "some-other-vendor")
