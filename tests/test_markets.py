"""Match-intelligence markets (ADR 0041) on the REAL committed 1140-match EPL fixture (3 seasons with
corners/cards/shots). Models are fit only on matches finished before a cutoff and scored on the matches
after it. Nothing is simulated."""

import json
import shutil
import sys
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci_real_data_sanity import AS_OF, FIXTURE_ROOT  # noqa: E402
from src.config import config_dir_for, load_config  # noqa: E402
from src.data.dataset import resolve_dataset  # noqa: E402
from src.data.pipeline import run_pipeline  # noqa: E402
from src.markets.data import load_stat_matches  # noqa: E402
from src.markets.model import count_pmf, fit_markets, scoreline_matrix  # noqa: E402
from src.markets.predict import devig, intelligence  # noqa: E402
from src.markets.run import fixture_key, write_artifact  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("mk") / "proj"
    shutil.copytree(FIXTURE_ROOT, root, ignore=shutil.ignore_patterns("artifacts", "__pycache__"))
    cfg_text = (REPO_ROOT / "configs" / "markets.yaml").read_text(encoding="utf-8")
    # the fixture world is the research CSV dataset (it carries corners/cards/shots)
    (config_dir_for(root) / "markets.yaml").write_text(
        cfg_text.replace("history_source: openfootball", "history_source: football-data"), encoding="utf-8"
    )
    run_pipeline(root, "research", as_of=AS_OF)
    cfg = load_config("markets", config_dir_for(root))
    ref = resolve_dataset(root / load_config("data", config_dir_for(root)).processed_dir)
    ms = load_stat_matches(ref)
    cut = ms[760 + 60].kickoff_utc  # early in the third season
    return {"root": root, "cfg": cfg, "ms": ms, "cut": cut, "ref": ref}


def test_fit_uses_only_matches_available_before_the_cutoff(world):
    mm = fit_markets(world["ms"], world["cut"], world["cfg"])
    n_before = sum(1 for m in world["ms"] if m.available_at <= world["cut"])
    assert mm.n_matches == n_before < len(world["ms"])
    assert -0.3 <= mm.rho <= 0.3 and set(mm.counts) == {"corners", "yellow_cards", "shots_on_target"}


def test_intelligence_is_internally_consistent(world):
    mm = fit_markets(world["ms"], world["cut"], world["cfg"])
    home, away = world["ms"][-1].home_id, world["ms"][-1].away_id
    i = intelligence(mm, home, away, "EPL")
    r = i["result"]
    assert sum(r["model_probs"].values()) == pytest.approx(1.0)
    assert r["double_chance"]["1X"] == pytest.approx(
        r["headline_probs"]["home"] + r["headline_probs"]["draw"]
    )
    assert (
        sum(s["p"] for s in i["scorelines"]) <= 1.0 and i["most_likely_score"] == i["scorelines"][0]["score"]
    )
    overs = [x["over"] for x in i["goals"]["over_under"]]
    assert overs == sorted(overs, reverse=True)  # P(over) falls as the line rises
    for c in i["counts"].values():
        o = [x["over"] for x in c["over_under"]]
        assert o == sorted(o, reverse=True) and c["expected"]["total"] > 0
    probs = [t["probability"] for t in i["tips"]]
    assert probs == sorted(probs, reverse=True) and all(0.5 <= p <= 1 for p in probs)
    assert i["result"]["headline_basis"] == "model"


def test_market_odds_drive_the_headline_and_scorelines_stay_consistent(world):
    mm = fit_markets(world["ms"], world["cut"], world["cfg"])
    m = world["ms"][-1]
    odds = (2.0, 3.5, 4.0)
    i = intelligence(mm, m.home_id, m.away_id, "EPL", odds, blend_weight=1.0)
    q = devig(odds)
    assert i["result"]["headline_probs"]["home"] == pytest.approx(q[0])
    assert "market" in i["result"]["headline_basis"]
    half = intelligence(mm, m.home_id, m.away_id, "EPL", odds, blend_weight=0.5)
    lo, hi = sorted([i["result"]["model_probs"]["home"], q[0]])
    assert lo <= half["result"]["headline_probs"]["home"] <= hi


def test_unknown_team_is_flagged_not_silently_known(world):
    mm = fit_markets(world["ms"], world["cut"], world["cfg"])
    i = intelligence(mm, "ENG_brand_new_club", world["ms"][-1].away_id, "EPL")
    assert any("no history" in f for f in i["data_quality"]["flags"])


def test_distributions_are_proper(world):
    cfg = world["cfg"]
    assert scoreline_matrix(1.4, 1.1, -0.05, cfg.max_goals).sum() == pytest.approx(1.0)
    for alpha in (0.0, 0.07):
        assert count_pmf(9.5, alpha, 60).sum() == pytest.approx(1.0)


def test_out_of_sample_goals_model_beats_the_league_baseline(world):
    """Real held-out check on the fixture: fit before the cutoff, score the next 120 days."""
    cfg, ms, cut = world["cfg"], world["ms"], world["cut"]
    mm = fit_markets(ms, cut, cfg)
    test = [m for m in ms if cut <= m.kickoff_utc < cut + timedelta(days=120)]
    assert len(test) > 100
    ll_m, ll_b = [], []
    for m in test:
        x, y = min(m.home_goals, cfg.max_goals), min(m.away_goals, cfg.max_goals)
        lh, la = mm.goals.lambdas(m.home_id, m.away_id, m.league)
        ll_m.append(-np.log(scoreline_matrix(lh, la, mm.rho, cfg.max_goals)[x, y]))
        base = scoreline_matrix(mm.goals.mu_home[m.league], mm.goals.mu_away[m.league], 0.0, cfg.max_goals)
        ll_b.append(-np.log(base[x, y]))
    assert np.mean(ll_m) < np.mean(ll_b)


def test_artifact_is_immutable_hashed_and_served_by_the_api_and_dashboard(world, tmp_path):
    from fastapi.testclient import TestClient

    from src.api.app import create_app
    from src.dashboard.render import render_html
    from src.dashboard.viewmodel import build_viewmodel

    shutil.copytree(REPO_ROOT / "configs", tmp_path / "configs")
    mm = fit_markets(world["ms"], world["cut"], world["cfg"])
    now = world["cut"].astimezone(UTC)
    kickoff = now + timedelta(days=1)
    key = fixture_key("ENG_arsenal", "ENG_leeds_united", kickoff)
    p = write_artifact(tmp_path, mm, "cfg", now, key, "fdorg-1", "EPL", "ENG_arsenal", "ENG_leeds_united", kickoff, "dv-x", "fv2", None)  # fmt: skip
    assert p is not None and json.loads(p.read_text(encoding="utf-8"))["content_hash"]
    again = write_artifact(tmp_path, mm, "cfg", now, key, "fdorg-1", "EPL", "ENG_arsenal", "ENG_leeds_united", kickoff, "dv-x", "fv2", None)  # fmt: skip
    assert again is None  # never overwritten
    c = TestClient(create_app(tmp_path, keys=["k"], now_fn=lambda: now))
    r = c.get(f"/v1/fixtures/{key}/intelligence", headers={"X-API-Key": "k"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert (
        d["model_version"].startswith("markets-") and d["data_version"] == "dv-x" and d["counts"]["corners"]
    )
    assert c.get("/v1/fixtures/nope/intelligence", headers={"X-API-Key": "k"}).status_code == 404
    tips = c.get("/v1/tips?min_probability=0.5", headers={"X-API-Key": "k"}).json()
    assert tips["page"]["total"] >= 1 and tips["data"][0]["probability"] >= tips["data"][-1]["probability"]
    assert c.get("/v1/tips?min_probability=0.2", headers={"X-API-Key": "k"}).status_code == 422
    html = render_html(build_viewmodel(tmp_path, now))
    assert "Match intelligence" in html and "Most likely score" in html


def test_evaluate_runs_on_the_fixture_without_touching_final_test_seasons(world):
    from src.markets.evaluate import evaluate, main, to_markdown

    rep = evaluate(world["root"])
    assert rep["final_test_seasons_loaded"] is False and rep["n_matches"] > 300
    names = {r["market"] for r in rep["results"]}
    assert any("correct score" in n for n in names) and any("corners" in n for n in names)
    assert all(
        r["verdict"] in ("model better", "baseline better", "no clear difference") for r in rep["results"]
    )
    md = to_markdown(rep)
    assert "final-test seasons NOT loaded" in md and "no market is declared a winner" in md
    assert main(["--root", str(world["root"])]) == 0
    assert (world["root"] / "artifacts" / "markets" / "evaluation" / "evaluation.json").exists()


def test_latest_exact_odds_reads_only_complete_exact_sets(tmp_path):
    from datetime import datetime

    from src.markets.run import latest_exact_odds
    from src.odds.store import OddsStore
    from src.odds.theoddsapi import parse_event

    cap = json.loads(
        (REPO_ROOT / "tests/fixtures/real_provider_captures/theoddsapi_event.json").read_text(
            encoding="utf-8"
        )
    )
    ev = parse_event(cap["event"], datetime.fromisoformat(cap["captured_at_utc"]), "sha")
    store = OddsStore(tmp_path, ev["fixture_id"], "odds_remote")
    store.write_meta({"home_id": "ENG_arsenal", "away_id": "ENG_leeds_united", "league": "EPL", "kickoff_utc": ev["kickoff_utc"].isoformat()})  # fmt: skip
    store.add(ev["quotes"])
    day = ev["kickoff_utc"].date().isoformat()
    got = latest_exact_odds(tmp_path, "ENG_arsenal", "ENG_leeds_united", day)
    assert got["quality"] == "exact" and len(got["odds"]) == 3 and got["bookmaker"]
    assert latest_exact_odds(tmp_path, "ENG_arsenal", "ENG_chelsea", day) is None


def test_openfootball_world_loads_history_upcoming_and_falls_back_to_the_cache(tmp_path, monkeypatch):
    """Real captured openfootball response served over a real loopback socket (ADR 0044)."""
    import threading
    from datetime import datetime
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from src.data.teams import TeamDirectory
    from src.markets.of_source import load_world

    web = tmp_path / "web" / "2026-27"
    web.mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / "tests/fixtures/real_provider_captures/openfootball_en1_2026_trimmed.json",
        web / "en.1.json",
    )
    (web / "es.1.json").write_text('{"name": "empty", "matches": []}', encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(web.parent)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENFOOTBALL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    cfg = load_config("markets").model_copy(update={"history_start_year": 2026})
    directory = TeamDirectory.load(REPO_ROOT / "configs" / "team_aliases.yaml")
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    w = load_world(tmp_path, directory, cfg, now)
    assert len(w.history) == 7 and len(w.upcoming) == 2 and w.stale_files == [] and w.unresolved == 0
    m = w.history[0]
    assert m.available_at - m.kickoff_utc == timedelta(hours=cfg.result_lag_hours)  # inferred, labelled
    assert all(v is None for v in m.home_stats.values())  # no corners/cards in the public-domain source
    assert (
        w.data_version.startswith("dv-of-")
        and load_world(tmp_path, directory, cfg, now).data_version == w.data_version
    )
    srv.shutdown()
    monkeypatch.setenv("OPENFOOTBALL_BASE_URL", "http://127.0.0.1:9")
    stale = load_world(tmp_path, directory, cfg, now)  # server gone: the cache is used and reported
    assert len(stale.history) == 7 and len(stale.stale_files) == 2 and stale.data_version == w.data_version
    shutil.rmtree(tmp_path / "artifacts")
    with pytest.raises(Exception, match="unreachable and no cache"):
        load_world(tmp_path, directory, cfg, now)


def test_default_config_uses_the_licence_clean_source():
    cfg = load_config("markets")
    assert cfg.history_source == "openfootball" and cfg.result_lag_hours > 0
