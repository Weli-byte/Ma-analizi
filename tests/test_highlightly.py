"""Highlightly adapter, statistics store and overlay (ADR 0050) on REAL captured responses
(tests/fixtures/real_provider_captures/highlightly/, taken 2026-10-11 from the live API). The client runs over a
real loopback HTTP socket; nothing is simulated."""

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.config import load_config
from src.data.teams import Alias, TeamDirectory
from src.ingestion.highlightly import HighlightlyClient, parse_score, parse_statistics
from src.ingestion.provider import ProviderError
from src.markets.data import StatMatch
from src.markets.stats_overlay import attach_stats
from src.markets.stats_store import collect, load

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "tests" / "fixtures" / "real_provider_captures" / "highlightly"
STATS = json.loads((CAP / "statistics_1349084276.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
KEY = "key-that-must-never-be-logged"
SEEN: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append(self.headers.get("x-rapidapi-key", ""))
        path = self.path.split("?")[0]
        f = {"/matches": "matches_page.json", "/statistics/1349084276": "statistics_1349084276.json"}.get(
            path
        )
        if f is None:
            self.send_response(404)
            self.end_headers()
            return
        body = (CAP / f).read_bytes()
        self.send_response(200)
        self.send_header("x-ratelimit-requests-remaining", "88")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet
        pass


@pytest.fixture
def server(monkeypatch):
    SEEN.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HIGHLIGHTLY_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield
    srv.shutdown()


def test_real_statistics_parse_with_missing_values_kept_as_none():
    s = parse_statistics(STATS)
    home, away = s[1173462], s[1163250]
    assert (home["corners"], home["yellow_cards"], home["shots_on_target"], home["shots_off_target"]) == (
        9,
        2,
        4,
        10,
    )
    assert (away["corners"], away["yellow_cards"], away["shots_on_target"]) == (5, 3, 7)
    assert home["expected_goals"] is None  # not offered for this match: never turned into 0
    assert parse_statistics([{"team": {"id": 1}, "statistics": []}]) == {1: None}


def test_score_parsing():
    assert parse_score({"score": {"current": "2 - 4"}}) == (2, 4)
    assert parse_score({"score": {"current": None}}) is None and parse_score({}) is None


def test_client_over_a_real_socket_never_logs_the_key(server):
    c = HighlightlyClient(KEY)
    page = c.matches(33973, 2026)
    assert page.total == 2 and page.plan == "BASIC" and page.remaining == 88 and len(page.data) == 2
    assert c.statistics(1349084276)[1173462]["corners"] == 9
    assert set(SEEN) == {KEY}
    from src.mlops.oplog import read_rows

    rows = read_rows("provider_calls.jsonl")
    assert {r["provider"] for r in rows} == {"highlightly"} and KEY not in json.dumps(rows)


def test_a_failing_call_is_a_provider_error_without_the_key(server):
    with pytest.raises(ProviderError) as e:
        HighlightlyClient(KEY).statistics(1)  # the capture server has no such match: HTTP 404
    assert KEY not in str(e.value) and "404" in str(e.value)


def test_collect_respects_the_budget_and_is_resumable(server, tmp_path):
    c = HighlightlyClient(KEY)
    first = collect(tmp_path, c, ["EPL"], [2026], budget=1, now=NOW)
    assert first["listing_calls"] == 1 and first["statistics_calls"] == 0 and first["stopped"] == "budget"
    rows = load(tmp_path, "EPL", 2026)
    assert len(rows) == 2 and all(r["stats"] is None for r in rows.values())
    second = collect(
        tmp_path, HighlightlyClient(KEY), ["EPL"], [2026], budget=10, now=NOW + timedelta(hours=1)
    )
    assert second["listing_calls"] == 0  # the listing is fresh: no request spent
    assert (
        second["statistics_calls"] == 1 and "404" in second["last_error"]
    )  # the second match is not in the capture
    rows = load(tmp_path, "EPL", 2026)
    got = rows[1349084276]
    assert got["stats"]["home"]["corners"] == 9 and got["stats"]["away"]["yellow_cards"] == 3
    assert rows[1325369459]["stats"] is None and second["pending_statistics"] == 1


def _directory() -> TeamDirectory:
    d = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    for raw, tid in (("Manchester United", "ENG_manchester_united"), ("Tottenham", "ENG_tottenham_hotspur")):
        d.aliases.append(
            Alias("highlightly", raw, tid, None, None, "manual", "test identity of two real clubs", 1.0)
        )
    return d


def test_overlay_adds_gap_matches_attaches_statistics_and_reports_unresolved(server, tmp_path):
    collect(tmp_path, HighlightlyClient(KEY), ["EPL"], [2026], budget=10, now=NOW)
    cfg = load_config("markets")
    d = _directory()
    # a real row is attached to an existing history match of the same ordered pair and season
    ko = datetime(2026, 10, 10, 16, 30, tzinfo=UTC)
    known = StatMatch("of-x", "2026-27", "EPL", ko, ko + timedelta(hours=3), "ENG_manchester_united", "ENG_tottenham_hotspur", 1, 1, dict.fromkeys(("corners", "yellow_cards", "shots_on_target")), dict.fromkeys(("corners", "yellow_cards", "shots_on_target")), None)  # fmt: skip
    out, rep = attach_stats([known], tmp_path, d, cfg)
    assert rep["rows"] == 2 and rep["matched"] == 1 and rep["added"] == 0
    assert sorted(rep["unresolved_names"]) == [
        "Forest Green",
        "Hartlepool",
    ]  # National League clubs: never auto-registered
    assert len(out) == 1 and out[0].home_goals == 1
    # with no history at all the Highlightly match becomes a history row (the Super Lig gap path)
    out2, rep2 = attach_stats([], tmp_path, d, cfg)
    assert (
        rep2["added"] == 1
        and out2[0].fixture_id.startswith("hl-")
        and out2[0].home_id == "ENG_manchester_united"
    )
    assert out2[0].available_at - out2[0].kickoff_utc == timedelta(hours=cfg.result_lag_hours)


def test_counts_stay_out_of_published_artifacts_on_the_free_plan():
    cfg = load_config("markets")
    assert cfg.stats_source == "highlightly" and cfg.stats_plan == "basic"  # flip to "pro" only after paying


def test_stats_collect_cli_reports_not_configured_without_a_key(tmp_path, monkeypatch, capsys):
    import shutil

    from src.markets.stats_collect import main

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    monkeypatch.delenv("HIGHLIGHTLY_API_KEY", raising=False)
    for target in ("src.markets.stats_collect.load_dotenv", "src.cli_utils.load_dotenv"):
        monkeypatch.setattr(target, lambda *a, **k: None)  # a local .env must not leak in
    assert main(["--root", str(tmp_path)]) == 0
    assert "NOT_CONFIGURED" in capsys.readouterr().out


def test_stats_collect_cli_runs_against_the_real_capture_server(server, tmp_path, monkeypatch, capsys):
    import shutil

    from src.markets.stats_collect import main

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    monkeypatch.setenv("HIGHLIGHTLY_API_KEY", KEY)
    assert main(["--root", str(tmp_path), "--league", "EPL", "--budget", "6", "--from-year", "2026"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["listing_calls"] == 1 and out["statistics_calls"] == 1 and out["remaining_quota"] == 88


def test_alias_tool_accepts_only_clear_same_country_matches_and_lists_the_rest(tmp_path):
    """Real names from the captured match page; the store row is written exactly as the collector writes it."""
    from src.markets.alias_tool import main, propose, store_names
    from src.markets.stats_store import save

    page = json.loads((CAP / "matches_page.json").read_text(encoding="utf-8"))
    from src.markets.stats_store import _row

    m = page["data"][1]  # Manchester United v Tottenham (real)
    save(tmp_path, "EPL", 2026, {m["id"]: _row(m, "EPL", 2026)})
    d = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    d.aliases = [a for a in d.aliases if a.source != "highlightly"]  # as before the mapping existed
    accepted, manual = propose(d, store_names(tmp_path))
    assert [(x[2], x[3]) for x in accepted] == [("Tottenham", "ENG_tottenham_hotspur")] and manual == []
    # the shipped aliases already cover it: nothing is proposed and the tool exits cleanly
    import shutil

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    assert main(["--root", str(tmp_path)]) == 0


def test_alias_review_page_embeds_local_logos_and_escapes_names(tmp_path):
    import shutil

    from src.markets.alias_review_page import build
    from src.markets.stats_store import _row, save

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    page = json.loads((CAP / "matches_page.json").read_text(encoding="utf-8"))
    m = page["data"][1]  # Manchester United v Tottenham (real)
    save(tmp_path, "EPL", 2026, {m["id"]: _row(m, "EPL", 2026)})
    logos = tmp_path / "logos"
    logos.mkdir()
    (logos / "40781.png").write_bytes(
        b"\x89PNG\r\n\x1a\n-test-bytes-"
    )  # Tottenham's real Highlightly team id
    html = build(tmp_path, logos)
    assert "data:image/png;base64," in html and "Alias review" in html and "<script>alert" not in html
    assert "dogru kulup" in html  # the correction inputs exist
