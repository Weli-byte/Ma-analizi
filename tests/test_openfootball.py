"""openfootball (public domain) adapter over a REAL captured response (trimmed): parsing, the real
two score shapes, UTC conversion flagged as inferred, ingestion with team resolution, de-duplication and
the real client over a loopback HTTP socket. Nothing is simulated."""

import json
import shutil
import threading
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.data.teams import TeamDirectory
from src.ingestion.openfootball import OpenFootballProvider, parse
from src.ingestion.provider import ProviderError
from src.ingestion.results import ingest_finished, ingested_matches, read_store

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "tests" / "fixtures" / "real_provider_captures" / "openfootball_en1_2026_trimmed.json"
RAW = CAP.read_bytes()
DIRECTORY = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def test_real_capture_parses_finished_bare_list_and_scheduled_matches():
    fx = parse(RAW, "EPL", "2026-27")
    assert [f.status_raw for f in fx] == ["FT"] * 7 + ["SCHEDULED"] * 2
    assert (fx[0].home_goals, fx[0].away_goals) == (3, 0)
    assert (fx[6].home_goals, fx[6].away_goals) == (0, 0) and fx[6].extra["score_shape"] == "list"
    assert fx[7].home_goals is None and fx[7].away_goals is None  # never a guessed score
    # 20:00 London in August is 19:00 UTC; the zone is inferred and says so
    assert fx[0].kickoff_utc == datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
    assert fx[0].extra["kickoff_time_zone_inferred"] == "Europe/London"


def test_ingestion_resolves_teams_and_never_stores_unfinished_or_duplicates(tmp_path):
    fx = parse(RAW, "EPL", "2026-27")
    kw = {"source": "openfootball", "id_prefix": "of", "now": NOW}
    c1 = ingest_finished(tmp_path, fx, DIRECTORY, "ENG", "EPL", **kw)
    assert c1 == {"new": 7, "known": 0, "unresolved": 0, "not_finished": 2}
    c2 = ingest_finished(tmp_path, fx, DIRECTORY, "ENG", "EPL", **kw)
    assert c2["new"] == 0 and c2["known"] == 7  # idempotent
    rows = read_store(tmp_path)
    assert rows[0]["source"] == "openfootball" and rows[0]["fixture_id"].startswith("of-2026-08-21")
    # a result is usable only from the moment WE saw it, never backdated to kickoff
    assert all(m.result_available_at_utc == NOW for m in ingested_matches(tmp_path))


def test_unknown_team_is_counted_unresolved_not_auto_registered(tmp_path):
    raw = json.loads(RAW)
    raw["matches"][0]["team1"] = "Invented Rovers FC"
    fx = parse(json.dumps(raw).encode(), "EPL", "2026-27")
    c = ingest_finished(tmp_path, fx, DIRECTORY, "ENG", "EPL", source="openfootball", id_prefix="of", now=NOW)
    assert c["unresolved"] == 1 and c["new"] == 6


@pytest.fixture
def server(tmp_path, monkeypatch):
    web = tmp_path / "web" / "2026-27"
    web.mkdir(parents=True)
    shutil.copy(CAP, web / "en.1.json")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(web.parent)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENFOOTBALL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield
    srv.shutdown()


def test_client_over_a_real_http_socket(server):
    fx = OpenFootballProvider().list_fixtures("EPL", 2026)
    assert len(fx) == 9
    from src.mlops.oplog import read_rows

    (row,) = read_rows("provider_calls.jsonl")
    assert row["provider"] == "openfootball" and row["ok"]


def test_connection_failure_is_a_provider_error(monkeypatch):
    monkeypatch.setenv("OPENFOOTBALL_BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(ProviderError):
        OpenFootballProvider(timeout=2).list_fixtures("EPL", 2026)
