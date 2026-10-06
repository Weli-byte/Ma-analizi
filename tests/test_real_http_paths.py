"""Real client code over a real HTTP socket, served REAL captured provider responses.

A loopback http.server (stdlib) serves files that hold real responses captured on 2026-10-02 (subsets
of the real JSON, values unmodified). The production clients are pointed at it through the loopback-only
endpoint override (`src/ingestion/endpoints.py`), so URL building, the HTTP request, parsing,
processing and the ops log all run for real; only the remote host differs. Nothing is monkeypatched."""

import json
import shutil
import threading
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.teams import TeamDirectory
from src.ingestion.endpoints import endpoint
from src.ingestion.football_data_org import FootballDataOrgProvider
from src.ingestion.lineups import EspnLineupProvider
from src.live import run as live_run
from src.live.events import MatchStatus
from src.live.feeds import FootballDataOrgLiveFeed, OpenLigaDBFeed
from src.odds import run as odds_run
from src.odds.store import OddsStore

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "tests" / "fixtures" / "real_provider_captures"
FDORG = json.loads((CAP / "fdorg_matches.json").read_text(encoding="utf-8"))["matches"]
OLDB = json.loads((CAP / "openligadb_bl1_matches.json").read_text(encoding="utf-8"))["matches"]
ESPN_SB = json.loads((CAP / "espn_eng1_scoreboard.json").read_text(encoding="utf-8"))
ESPN_RO = json.loads((CAP / "espn_rosters.json").read_text(encoding="utf-8"))["events"]
OBSERVED = datetime.fromisoformat(ESPN_SB["captured_at_utc"])


def _write(base: Path, rel: str, obj) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def server(tmp_path, monkeypatch):
    web = tmp_path / "web"
    _write(web, "competitions/PL/matches", {"matches": FDORG})  # subset of a real list response
    _write(
        web, "matches", {"resultSet": {"count": 0}, "matches": []}
    )  # real: nothing in play at capture time
    _write(web, "getmatchdata/bl1", OLDB)  # real Bundesliga matches (none "current" by the clock)
    _write(web, "getmatchdata/83183", OLDB[0])  # the real 7-0 match (penalty at 39')
    _write(web, "eng.1/scoreboard", {"events": ESPN_SB["events"]})  # real ESPN scoreboard events
    _write(
        web, "eng.1/summary", ESPN_RO["upcoming_401879268"]["summary"]
    )  # real: rosters empty until announced
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(web)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for name in ("FOOTBALL_DATA_ORG_BASE_URL", "ESPN_BASE_URL", "OPENLIGADB_BASE_URL"):
        monkeypatch.setenv(name, base)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    yield SimpleNamespace(base=base, web=web)
    srv.shutdown()


def directory():
    return TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")


# ------------------------------------------------------------------------------ endpoint guard
def test_endpoint_override_is_loopback_only(monkeypatch):
    monkeypatch.setenv("X_BASE", "https://evil.example")
    with pytest.raises(ValueError, match="loopback"):
        endpoint("X_BASE", "https://real.example")
    monkeypatch.setenv("X_BASE", "http://127.0.0.1:9/")
    assert endpoint("X_BASE", "https://real.example") == "http://127.0.0.1:9"
    monkeypatch.delenv("X_BASE")
    assert endpoint("X_BASE", "https://real.example") == "https://real.example"


# ----------------------------------------------------------------------------- football-data.org
def test_fixture_adapter_over_http_returns_the_real_matches(server):
    raws = FootballDataOrgProvider("k").list_fixtures("PL", "2026-27")
    assert {(r.provider_fixture_id, r.status_raw) for r in raws} == {("560593", "NS"), ("560542", "FT")}
    assert next(r for r in raws if r.provider_fixture_id == "560542").home_goals == 3


def test_live_feed_over_http_lists_in_play_and_polls(server):
    feed = FootballDataOrgLiveFeed("k")
    assert feed.list_live_all() == []
    listed = feed.list_live("PL")
    assert len(listed) == 2 and {m["status"] for m in listed} == {"FINISHED", "TIMED"}


# ----------------------------------------------------------------------------------- OpenLigaDB
def test_openligadb_poll_over_http_parses_the_real_match(server):
    snap = OpenLigaDBFeed().poll("83183", datetime.now(UTC))
    assert snap.status == MatchStatus.FINISHED and snap.score == (7, 0) and len(snap.events) == 7
    assert OpenLigaDBFeed().list_current("bl1", datetime.now(UTC)) == []  # Sept matches are not "current"


def test_live_runner_over_http_reports_no_live_match(server, tmp_path, capsys):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "configs", root / "configs")
    (root / "data" / "processed").mkdir(parents=True)
    (root / "data" / "processed" / "CURRENT.json").write_text("{}")
    ref = SimpleNamespace(data_version="dv-000000000000")
    assert live_run.tick_openligadb(root, "bl1", OpenLigaDBFeed(), ref) == []
    assert live_run.tick_fdorg(root, "ALL", FootballDataOrgLiveFeed("k"), directory(), ref, {}) == []


def test_in_play_payload_capture_only_keeps_real_in_play_matches(tmp_path):
    finished = next(m for m in FDORG if m["status"] == "FINISHED")
    live_run.capture_first_in_play(tmp_path, finished)
    assert not (tmp_path / "artifacts" / "live" / "_captures").exists()  # finished: nothing captured
    live_run.capture_first_in_play(tmp_path, {**finished, "status": "IN_PLAY"})
    (cap,) = (tmp_path / "artifacts" / "live" / "_captures").glob("fdorg_*_IN_PLAY.json")
    assert json.loads(cap.read_text(encoding="utf-8"))["match"]["id"] == finished["id"]


# ------------------------------------------------------------------------------------------- ESPN
def test_odds_collection_over_http_stores_approximate_quotes_once(server, tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "configs", root / "configs")
    lines = odds_run.collect(root, "PL", OBSERVED)
    assert any("Arsenal v Leeds United: 6 new quote" in x for x in lines)
    assert any("the-odds-api: NOT_CONFIGURED" in x for x in lines)  # no key: said so, no exact odds
    quotes = OddsStore(root, "espn-401879268").quotes()
    assert {q.timestamp_quality for q in quotes} == {"approximate", "unknown"}
    again = odds_run.collect(root, "PL", OBSERVED)
    assert any("0 new quote" in x for x in again)  # same observation: de-duplicated


def test_lineup_provider_over_http_finds_the_event_and_reports_unannounced(server):
    prov = EspnLineupProvider(directory())
    kickoff = datetime(2026, 10, 10, 11, 30, tzinfo=UTC)
    eid = prov.find_event_id("EPL", "ENG_arsenal", "ENG_leeds_united", kickoff)
    assert eid == "401879268"
    block = prov.fetch_lineups("EPL", eid, OBSERVED)
    assert block["status"] == "UNKNOWN" and block["reason"] == "not_announced_yet"
    assert prov.find_event_id("EPL", "ENG_chelsea", "ENG_everton", kickoff) is None


def test_lineup_provider_over_http_parses_an_announced_lineup(server):
    _write(server.web, "eng.1/summary", ESPN_RO["finished_401879301"]["summary"])  # real announced rosters
    block = EspnLineupProvider(directory()).fetch_lineups("EPL", "401879301", OBSERVED + timedelta(seconds=1))
    assert block["status"] == "OBSERVED" and len(block["home"]["starters"]) == 11


def test_provider_calls_over_http_land_in_the_ops_log(server):
    from src.mlops.oplog import read_rows

    FootballDataOrgProvider("k").list_fixtures("PL", "2026-27")
    OpenLigaDBFeed().poll("83183", datetime.now(UTC))
    rows = read_rows("provider_calls.jsonl")
    assert {(r["provider"], r["ok"]) for r in rows} == {("football-data-org", True), ("openligadb", True)}
