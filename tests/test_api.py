"""Public API (S18, ADR 0035): contract, auth, rate-limit, pagination and error tests over REAL
artifacts (the captured Arsenal-Leeds forecast run, the real exact The Odds API response and the real
committed 20-match benchmark). No mock provider is involved: the service reads files."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import RateLimiter, create_app
from src.odds.store import OddsStore
from src.odds.theoddsapi import parse_event

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "tests" / "fixtures" / "real_provider_captures"
NOW = datetime(2026, 10, 5, tzinfo=UTC)
KEY = "test-key-123"
FID = "ENG_arsenal__ENG_leeds_united__2026-10-10"
H = {"X-API-Key": KEY}


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    r = tmp_path_factory.mktemp("api")
    shutil.copytree(ROOT / "configs", r / "configs")
    run = r / "artifacts" / "llm_runs" / "forecast_fdorg-560593_20261002T080540Z"
    run.mkdir(parents=True)
    shutil.copy(CAP / "forecast_arsenal_leeds_predictions.jsonl", run / "predictions.jsonl")
    (run / "snapshot.json").write_text(
        json.dumps(
            {
                "fixture_id": "fdorg-560593",
                "home_team_id": "ENG_arsenal",
                "away_team_id": "ENG_leeds_united",
                "league_id": "EPL",
                "kickoff_utc": "2026-10-10T11:30:00+00:00",
            }  # fmt: skip
        ),
        encoding="utf-8",
    )
    sample = json.loads((CAP / "theoddsapi_event.json").read_text(encoding="utf-8"))
    ev = parse_event(sample["event"], datetime.fromisoformat(sample["captured_at_utc"]), "sha")
    store = OddsStore(r, ev["fixture_id"], "odds_remote")
    store.write_meta(
        {
            "home_id": "ENG_arsenal",
            "away_id": "ENG_leeds_united",
            "league": "EPL",
            "kickoff_utc": ev["kickoff_utc"].isoformat(),
        }  # fmt: skip
    )
    store.add(ev["quotes"])
    shutil.copytree(
        ROOT / "reports" / "benchmarks" / "llm_historical_20matches_20261002",
        r / "artifacts" / "llm_runs" / "benchmark_historical_dv-x_1",
    )
    return r


def client(root, **kw):
    return TestClient(create_app(root, keys=[KEY], now_fn=lambda: NOW, **kw), raise_server_exceptions=False)


def test_health_is_open_and_never_leaks_keys(root):
    r = client(root).get("/v1/health")
    assert r.status_code == 200 and r.json()["data"]["status"] == "ok"
    assert KEY not in r.text and r.headers["X-Request-ID"]


def test_data_routes_require_a_valid_key_with_the_error_schema(root):
    c = client(root)
    for headers in ({}, {"X-API-Key": "wrong"}):
        r = c.get("/v1/fixtures", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "http_error"
        assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]
        assert r.headers["WWW-Authenticate"] == "ApiKey"


def test_fails_closed_when_no_key_is_configured(root):
    r = TestClient(create_app(root, keys=[])).get("/v1/fixtures", headers=H)
    assert r.status_code == 503 and "FORECAST_API_KEYS" in r.json()["error"]["message"]


def test_fixtures_contract_and_pagination(root):
    c = client(root)
    body = c.get("/v1/fixtures", headers=H).json()
    assert body["meta"]["api_version"] == "v1" and body["meta"]["license_status"] == "RESEARCH_ONLY"
    assert body["page"] == {"limit": 20, "offset": 0, "total": 1}
    (f,) = body["data"]
    assert (
        f["fixture_id"] == FID
        and f["home"]["name"] == "Arsenal"
        and f["availability"]["lineups"] == "UNKNOWN"
    )
    assert "odds" not in f  # raw prices are never served (The Odds API terms, ADR 0043)
    m = f["market"]["the-odds-api"][0]
    assert m["quality"] == "exact" and abs(sum(m["implied_probs_devig"].values()) - 1) < 1e-9
    assert '"odds"' not in json.dumps(f) and "decimal" not in json.dumps(f["market"])
    assert c.get("/v1/fixtures?offset=1", headers=H).json()["data"] == []
    assert c.get("/v1/fixtures?league=LALIGA", headers=H).json()["page"]["total"] == 0
    bad = c.get("/v1/fixtures?limit=0", headers=H)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"


def test_predictions_carry_full_provenance(root):
    body = client(root).get(f"/v1/fixtures/{FID}/predictions", headers=H).json()
    assert body["page"]["total"] >= 3
    for p in body["data"]:
        pr = p["probabilities"]
        assert abs(pr["home"] + pr["draw"] + pr["away"] - 1) < 1e-6
        for k in ("prediction_id", "model_version", "data_version", "feature_version", "generated_at",
                  "information_cutoff", "provider"):  # fmt: skip
            assert p[k]
        assert "prompt_version" in p  # None = unknown (no call log in this root), never invented


def test_unknown_resources_are_404_with_the_error_schema(root):
    c = client(root)
    for path in (
        f"/v1/fixtures/{FID}x",
        "/v1/fixtures/nope/predictions",
        "/v1/teams/ENG_nobody/forecast",
        "/v1/zzz",
    ):
        r = c.get(path, headers=H)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_models_team_and_benchmarks(root):
    c = client(root)
    models = c.get("/v1/models", headers=H).json()["data"]
    assert {m["model_class"] for m in models} == {"LLM_REAL"}
    team = c.get("/v1/teams/ENG_arsenal/forecast", headers=H).json()["data"]
    assert team["fixtures"][0]["latest_predictions"]
    b = c.get("/v1/benchmarks", headers=H).json()["data"]
    assert (
        b["available"]
        and b["track"] == "HISTORICAL"
        and "memorized" in b["caveat"]
        and len(b["results"]) == 3
    )


def test_value_research_is_paper_only_and_uses_exact_quotes_only(root):
    data = client(root).get("/v1/value-research", headers=H).json()["data"]
    assert data["paper_only"] and "not betting advice" in data["disclaimer"]
    # real forecasts (2026-10-02) predate the real exact quotes (2026-10-08 capture): rows are ELIGIBLE
    assert data["rows"] and all(r["status"] in ("ELIGIBLE", "NOT_ELIGIBLE") for r in data["rows"])
    for r in data["rows"]:
        assert (r["ev"] is not None) == (r["status"] == "ELIGIBLE")  # no EV unless eligible


def test_rate_limit_returns_429_with_retry_after_and_resets_next_window(root):
    t = [1000.0]
    c = client(root, per_minute=3, clock=lambda: t[0])
    assert [c.get("/v1/models", headers=H).status_code for _ in range(3)] == [200, 200, 200]
    r = c.get("/v1/models", headers=H)
    assert r.status_code == 429 and r.json()["error"]["code"] == "http_error"
    assert int(r.headers["Retry-After"]) >= 1
    t[0] += 60
    assert c.get("/v1/models", headers=H).status_code == 200


def test_rate_limiter_is_per_key():
    t = [0.0]
    rl = RateLimiter(1, lambda: t[0])
    assert rl.check("a") is None and rl.check("a") is not None and rl.check("b") is None


def test_openapi_documents_every_v1_route(root):
    spec = client(root).get("/v1/openapi.json").json()
    for path in ("/v1/fixtures", "/v1/fixtures/{fixture_id}", "/v1/fixtures/{fixture_id}/predictions",
                 "/v1/models", "/v1/benchmarks", "/v1/teams/{team_id}/forecast", "/v1/value-research"):  # fmt: skip
        assert path in spec["paths"]


def test_value_picks_follow_thresholds_and_carry_caveats(root):
    c = client(root)
    loose = c.get("/v1/value-picks?min_edge=0&min_ev=0", headers=H).json()["data"]
    strict = c.get("/v1/value-picks?min_edge=1&min_ev=5", headers=H).json()["data"]
    assert strict["n"] == 0 and loose["n"] == len(loose["picks"])
    for p in loose["picks"]:
        assert p["edge"] >= 0 and p["ev_per_unit"] >= 0 and 0 <= p["stake_hint_pct_of_bankroll"] <= 2.0
        assert p["confidence"] in ("LOW", "MEDIUM") and p["caveats"] and p["selection"] in ("H", "D", "A")
        assert 1 <= p["models_agreeing"] <= p["models_evaluated"]
    assert c.get("/v1/value-picks?min_edge=2", headers=H).status_code == 422


def test_every_response_carries_the_required_football_data_org_attribution(root):
    r = client(root).get("/v1/health").json()
    assert "Football data provided by the Football-Data.org API" in r["meta"]["attribution"]


def test_value_research_serves_derived_values_not_raw_prices(root):
    rows = client(root).get("/v1/value-research", headers=H).json()["data"]["rows"]
    assert rows and all("odds" not in r for r in rows)
