"""Exact odds from The Odds API (ADR 0030). The quotes are a REAL response captured by the cloud
workflow on 2026-10-08 (Arsenal v Leeds United, three bookmakers, `last_update` per market); the
forecasts are the REAL LLM predictions made for the same match on 2026-10-02, i.e. BEFORE the quotes.
So the ELIGIBLE path below is exercised by real data end to end, with no injected timestamps."""

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.odds.paper import PaperLedger
from src.odds.run import collect_exact, sync_remote
from src.odds.schedule import WINDOWS_MIN, due_window
from src.odds.store import OddsStore, existing_store
from src.odds.theoddsapi import parse_event
from src.odds.value import closing_reference, value_row
from src.schemas import PredictionRecord

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "tests" / "fixtures" / "real_provider_captures"
SAMPLE = json.loads((CAP / "theoddsapi_event.json").read_text(encoding="utf-8"))
RECEIVED = datetime.fromisoformat(SAMPLE["captured_at_utc"])
PREDS = [
    PredictionRecord.from_json(x)
    for x in (CAP / "forecast_arsenal_leeds_predictions.jsonl").read_text(encoding="utf-8").splitlines()
    if x.strip()
]


def label(book: dict) -> str:
    return f"{book['title']} ({book['key']})"


def parsed():
    return parse_event(SAMPLE["event"], RECEIVED, "sha")


def test_real_response_gives_exact_quotes_with_measured_latency():
    ev = parsed()
    assert (
        ev["fixture_id"].startswith("oddsapi-")
        and ev["home_name"] == "Arsenal"
        and ev["away_name"] == "Leeds United"
    )
    q = ev["quotes"]
    assert len(q) == 9 and {x.selection for x in q} == {
        "H",
        "D",
        "A",
    }  # three bookmaker keys x three outcomes
    assert all(x.timestamp_quality == "exact" and x.snapshot_type == "pre_match" for x in q)
    for x in q:
        assert x.source_latency_s == pytest.approx((x.observed_at - x.provider_timestamp).total_seconds())
        assert 0 <= x.source_latency_s < 3600  # a measured latency, never guessed
    books = {x.bookmaker for x in q}
    assert len(books) == 3 and "William Hill (williamhill)" in books  # two feeds share the title "Betfair"
    # the market's own last_update is the provider timestamp (not the deprecated bookmaker-level one)
    wh = next(b for b in SAMPLE["event"]["bookmakers"] if b["title"] == "William Hill")
    assert next(
        x for x in q if x.bookmaker == "William Hill (williamhill)"
    ).provider_timestamp == datetime.fromisoformat(wh["markets"][0]["last_update"].replace("Z", "+00:00"))


def test_missing_or_future_provider_time_downgrades_to_approximate_not_exact():
    import copy

    ev = copy.deepcopy(SAMPLE["event"])
    del ev["bookmakers"][0]["markets"][0]["last_update"]
    assert {
        x.timestamp_quality
        for x in parse_event(ev, RECEIVED)["quotes"]
        if x.bookmaker == label(ev["bookmakers"][0])
    } == {"approximate"}
    skew = copy.deepcopy(SAMPLE["event"])
    skew["bookmakers"][0]["markets"][0]["last_update"] = (
        (RECEIVED + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    )
    assert {
        x.timestamp_quality
        for x in parse_event(skew, RECEIVED)["quotes"]
        if x.bookmaker == label(skew["bookmakers"][0])
    } == {"approximate"}


def test_an_unexpected_outcome_name_is_refused_not_guessed():
    import copy

    ev = copy.deepcopy(SAMPLE["event"])
    ev["bookmakers"][0]["markets"][0]["outcomes"][2]["name"] = "Tie"
    with pytest.raises(ValueError, match="unexpected h2h outcome"):
        parse_event(ev, RECEIVED)


def test_real_exact_quotes_make_the_real_forecasts_eligible_end_to_end():
    quotes = parsed()["quotes"]
    kickoff = parsed()["kickoff_utc"]
    assert PREDS and all(p.generated_at < RECEIVED < kickoff for p in PREDS)
    for p in PREDS:
        row = value_row(p, quotes, kickoff, "oddsapi-x")
        assert row.status == "ELIGIBLE" and row.source_latency_s is not None and row.source_latency_s < 3600
        probs = np.array([p.p_home, p.p_draw, p.p_away])
        assert row.ev == pytest.approx(tuple(probs * np.array(row.odds) - 1))
        assert sum(row.market_probs_devig) == pytest.approx(1.0) and row.overround is not None
    assert closing_reference(quotes, kickoff) is not None  # exact quotes can serve as the CLV reference


def test_paper_bet_from_a_real_exact_row_is_recorded_once(tmp_path):
    quotes, kickoff = parsed()["quotes"], parsed()["kickoff_utc"]
    row = value_row(PREDS[0], quotes, kickoff, "oddsapi-x")
    ledger = PaperLedger(tmp_path)
    bet = ledger.place(row, kickoff, -1.0, -1.0, 1.0)
    assert bet is not None and bet.fixture_id == "oddsapi-x" and bet.placed_at == row.observed_at
    assert ledger.place(row, kickoff, -1.0, -1.0, 1.0) is None


# ------------------------------------------------------------------------- credit-aware schedule
def test_collection_windows_open_only_around_the_stage_times():
    ko = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    at = lambda minutes_before: ko - timedelta(minutes=minutes_before)  # noqa: E731
    assert due_window(ko, at(1400)) == 0 and due_window(ko, at(80)) == 1
    assert due_window(ko, at(20)) == 2 and due_window(ko, at(5)) == 3
    for gap in (1300, 600, 200, 60, 50):
        assert due_window(ko, at(gap)) is None, gap  # between windows: no credit is spent
    assert due_window(ko, ko) is None and due_window(ko, ko + timedelta(minutes=1)) is None
    assert len(WINDOWS_MIN) == 4


def test_collect_exact_without_a_credit_never_calls_the_api(tmp_path, monkeypatch):
    monkeypatch.setenv("THE_ODDS_API_KEY", "never-sent")
    store = tmp_path / "data" / "artifacts" / "odds"
    store.mkdir(parents=True)
    (store / "_credits.json").write_text(json.dumps({"remaining": "10"}), encoding="utf-8")
    lines = collect_exact(ROOT, "PL", tmp_path / "data", force=True)  # reserve is 25 > 10 left
    assert "SKIPPED" in lines[0] and "reserve" in lines[0]


# ------------------------------------------------------------------ cloud branch -> local copy
def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, timeout=60)


def test_sync_remote_copies_the_cloud_branch_into_the_read_only_remote_store(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "odds-data")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    # what the cloud collector commits: real stored quotes
    src_store = OddsStore(origin, "oddsapi-abc")
    src_store.write_meta({"fixture_id": "oddsapi-abc", "kickoff_utc": parsed()["kickoff_utc"].isoformat(),
                          "home_id": "ENG_arsenal", "away_id": "ENG_leeds_united"})  # fmt: skip
    src_store.add(parsed()["quotes"])
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "collect")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    monkeypatch.setenv("DATA_REPO_URL", str(origin))  # the private data repo (here: a real local git repo)
    out = sync_remote(work)
    assert "files on the data repo branch odds-data" in out[0] and "updated" in out[0]
    remote = existing_store(work, "oddsapi-abc")
    assert remote is not None and remote.dir.parent.name == "odds_remote"
    assert {q.quote_id for q in remote.quotes()} == {q.quote_id for q in parsed()["quotes"]}
    assert "0 updated" in sync_remote(work)[0]  # idempotent
    assert "not available yet" in sync_remote(tmp_path / "work", "no-such-branch")[0]


def test_existing_store_prefers_local_and_returns_none_when_absent(tmp_path):
    assert existing_store(tmp_path, "oddsapi-none") is None
    OddsStore(tmp_path, "oddsapi-1", "odds_remote").write_meta({"x": 1})
    assert existing_store(tmp_path, "oddsapi-1").dir.parent.name == "odds_remote"
    OddsStore(tmp_path, "oddsapi-1", "odds").write_meta({"x": 2})
    assert existing_store(tmp_path, "oddsapi-1").dir.parent.name == "odds"


def test_request_url_uses_the_bookmaker_list_for_one_credit_and_puts_the_key_last():
    from src.odds.theoddsapi import TheOddsApiFeed

    url = TheOddsApiFeed("KEY123", ["pinnacle", "williamhill"]).url("PL")
    assert "bookmakers=pinnacle%2Cwilliamhill" in url and "regions" not in url
    assert url.endswith("apiKey=KEY123") and url.count("KEY123") == 1 and "/soccer_epl/" in url
    assert "regions=uk%2Ceu" in TheOddsApiFeed("KEY123").url(
        "PD"
    ) and "soccer_spain_la_liga" in TheOddsApiFeed("k").url("PD")


def test_data_repo_url_defaults_to_the_private_repo_and_can_be_overridden(monkeypatch):
    from src.data_repo import DEFAULT_URL, data_repo_url

    monkeypatch.delenv("DATA_REPO_URL", raising=False)
    assert data_repo_url() == DEFAULT_URL and "Ma-analizi2" in DEFAULT_URL
    monkeypatch.setenv("DATA_REPO_URL", "git@data-repo:x/y.git")
    assert data_repo_url() == "git@data-repo:x/y.git"
