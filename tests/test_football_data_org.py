"""S12: the real football-data.org adapter, run over a real HTTP socket against REAL captured
responses (`tests/fixtures/real_provider_captures/fdorg_competitions.json`, captured 2026-10-02).
Nothing is monkeypatched and no response is invented; the real error paths (429 rate limit, 403
invalid token) are exercised against the real server in `tests/integration/test_fdorg_errors_live.py`.
"""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import IngestionConfig
from src.ingestion.football_data_org import STATUS_MAP, FootballDataOrgProvider
from src.ingestion.provider import League
from src.ingestion.sync import IngestionProviderNotConfigured, resolve_ingestion_provider

CAP = json.loads(
    (Path(__file__).parent / "fixtures" / "real_provider_captures" / "fdorg_competitions.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture
def server(tmp_path, monkeypatch):
    web = tmp_path / "web"
    for rel, obj in (
        ("competitions", CAP["competitions"]),
        ("competitions/PL", CAP["competition_PL"]),
        ("competitions/PL/matches", CAP["matches_PL_2025"]),
    ):
        p = web / rel
        if p.is_dir() or (p.parent.exists() and p.parent.is_file()):
            raise AssertionError("fixture tree conflict")
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel == "competitions":  # a file and a directory of the same name cannot coexist: serve via index
            (web / "competitions").mkdir(exist_ok=True)
            (web / "competitions" / "index.html").write_text(json.dumps(obj), encoding="utf-8")
        elif rel == "competitions/PL":
            (web / "competitions" / "PL").mkdir(exist_ok=True)
            (web / "competitions" / "PL" / "index.html").write_text(json.dumps(obj), encoding="utf-8")
        else:
            p.write_text(json.dumps(obj), encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(web)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("FOOTBALL_DATA_ORG_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield SimpleNamespace(web=web)
    srv.shutdown()


def test_list_leagues_parses_the_real_competitions(server):
    leagues = FootballDataOrgProvider("key").list_leagues()
    codes = {(lg.name, lg.country) for lg in leagues}
    assert ("Premier League", "ENG") in codes and all(isinstance(lg, League) for lg in leagues)


def test_list_seasons_parses_the_real_season_dates(server):
    seasons = FootballDataOrgProvider("key").list_seasons("PL")
    assert [s.season for s in seasons] == ["2026-27", "2025-26", "2024-25"]
    assert seasons[0].start_date.isoformat() == "2026-08-21"


def test_list_fixtures_maps_real_status_and_score(server):
    fixtures = FootballDataOrgProvider("key").list_fixtures("PL", "2025-26")
    assert len(fixtures) == 3 and {f.status_raw for f in fixtures} == {"FT"}  # real finished matches
    first = fixtures[0]
    assert first.home_goals is not None and first.away_goals is not None
    assert first.kickoff_utc.isoformat().startswith("2025-08-15T") and first.provider_fixture_id.isdigit()
    assert first.league_id == "PL" and first.season == "2025-26"


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
    disabled = IngestionConfig(providers={"football-data-org": {"enabled": False, "api_key_env": "X"}})
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
