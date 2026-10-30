"""Phase 3 sprint 13: prediction lineage, provider smoke redaction and the frozen 138-match baseline (ADR 0091).

The forecasts are the REAL artifacts of the shared excerpt (`real_oddsens_excerpt.json`, written before the lineage block existed, so they must read `legacy_unknown`). A forecast WITH a lineage
block is produced by the real writer `src.markets.run.write_artifact` on a fitted model of TEST_ONLY matches (the writer is what the cloud job runs)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.lineage import baseline, query, record, smoke
from src.prospective.audit import recompute_hash
from src.web import i18n, pages

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"
KEY = "test-key-123"


@pytest.fixture
def tree(tmp_path):
    data = json.loads(EXCERPT.read_text(encoding="utf-8"))
    for key, m in data["matches"].items():
        d = tmp_path / "artifacts" / "markets" / key
        d.mkdir(parents=True)
        for f in m["intel"]:
            (d / f["file"]).write_text(json.dumps({**f["body"], "fixture_key": key}), encoding="utf-8")
    return tmp_path


def test_stage_boundaries():
    k = datetime(2026, 11, 1, 15, tzinfo=UTC)
    assert record.stage_of(k, k - timedelta(hours=30))[0] == "early" and record.stage_of(k, k - timedelta(hours=3))[0] == "t-24h"
    assert record.stage_of(k, k - timedelta(minutes=60))[0] == "t-90m" and record.stage_of(k, k - timedelta(minutes=10))[0] == "t-30m" and record.stage_of(k, k + timedelta(minutes=1))[0] == "post_kickoff"


def test_old_real_forecasts_are_labelled_legacy_and_never_back_filled(tree):
    key = next((tree / "artifacts" / "markets").iterdir()).name
    lin = query.lineage_for(tree, key)
    assert lin["n_versions"] >= 1 and lin["all_hashes_verified"] and lin["with_lineage_block"] == 0
    v = lin["versions"][-1]
    assert v["code_commit"] == query.LEGACY and v["lineage_schema"] == query.LEGACY and "derived" in v["stage"] and v["pre_kickoff"] and v["odds"]["bookmaker"]
    assert "odds" not in json.dumps(v["odds"]) or all(k in ("bookmaker", "observed_at", "quality") for k in v["odds"])     # no prices are returned
    assert query.lineage_for(tree, "NO__SUCH__2026-01-01") is None


def test_a_tampered_version_fails_its_hash_check(tree):
    d = next((tree / "artifacts" / "markets").iterdir())
    f = sorted(d.glob("intel-*.json"))[0]
    body = json.loads(f.read_text(encoding="utf-8"))
    body["intelligence"]["result"]["headline_probs"]["draw"] += 0.02
    f.write_text(json.dumps(body), encoding="utf-8")
    lin = query.lineage_for(tree, d.name)
    assert not lin["all_hashes_verified"] and lin["versions"][0]["hash_verified"] is False


def test_a_new_forecast_carries_the_lineage_block_inside_its_content_hash(tmp_path, monkeypatch):
    from src.config import load_config
    from src.markets.data import STATS, StatMatch
    from src.markets.model import fit_markets
    from src.markets.run import write_artifact

    monkeypatch.setenv("GITHUB_SHA", "abc1234567890")
    record.code_commit.cache_clear()
    cfg = load_config("markets")
    t0 = datetime(2026, 8, 1, 15, tzinfo=UTC)
    teams = [f"ENG_{c}" for c in "abcdefgh"]
    ms = [StatMatch(f"t{i}", "2026-27", "EPL", t0 + timedelta(hours=12 * i), t0 + timedelta(hours=12 * i + 3), teams[i % 8], teams[(i * 3 + 1) % 8] if (i * 3 + 1) % 8 != i % 8 else teams[(i + 1) % 8],
                    i % 3, (i + 1) % 2, dict.fromkeys(STATS), dict.fromkeys(STATS), None) for i in range(240)]  # TEST_ONLY  # fmt: skip
    now = t0 + timedelta(days=125)
    mm = fit_markets(ms, now, cfg)
    kickoff = now + timedelta(hours=2)
    odds = {"bookmaker": "Pinnacle (pinnacle)", "observed_at": now.isoformat(), "odds": [2.1, 3.3, 3.6], "quality": "exact"}
    path = write_artifact(tmp_path, mm, "cfg", now, "ENG_a__ENG_b__2026-09-16", "p1", "EPL", "ENG_a", "ENG_b", kickoff, "dv-test", "fv", odds)
    body = json.loads(path.read_text(encoding="utf-8"))
    lin = body["lineage"]
    assert lin["schema"] == "lineage-v1" and lin["code_commit"] == "abc1234567890" and lin["stage"] == "t-24h" and lin["history"]["matches"] == mm.n_matches
    assert lin["odds"] == {"bookmaker": "Pinnacle (pinnacle)", "observed_at": now.isoformat(), "quality": "exact"}
    assert recompute_hash(body) == body["content_hash"]
    tampered = {**body, "lineage": {**lin, "code_commit": "other"}}
    assert recompute_hash(tampered) != body["content_hash"]                                   # the lineage is covered by the hash
    assert write_artifact(tmp_path, mm, "cfg", now, "ENG_a__ENG_b__2026-09-16", "p1", "EPL", "ENG_a", "ENG_b", kickoff, "dv-test", "fv", odds) is None   # same path: refused, never overwritten
    view = query.lineage_for(tmp_path, "ENG_a__ENG_b__2026-09-16")
    assert view["with_lineage_block"] == 1 and view["versions"][0]["code_commit"] == "abc1234567890"
    record.code_commit.cache_clear()


def test_the_lineage_endpoint_needs_a_key_and_returns_versions(tree):
    c = TestClient(create_app(tree, keys=[KEY]), raise_server_exceptions=False)
    key = next((tree / "artifacts" / "markets").iterdir()).name
    assert c.get(f"/v1/fixtures/{key}/lineage").status_code in (401, 403)
    r = c.get(f"/v1/fixtures/{key}/lineage", headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json()["data"]["fixture_id"] == key and r.json()["data"]["versions"]
    assert c.get("/v1/fixtures/NO__SUCH__2026-01-01/lineage", headers={"X-API-Key": KEY}).status_code == 404


def test_the_match_page_note_says_legacy_or_shows_the_lineage():
    assert "künye kaydı başlamadan önce" in pages.lineage_note({})
    note = pages.lineage_note({"lineage": {"stage": "t-24h", "code_commit": "abcdef1234567", "history": {"matches": 1234}, "odds": {"bookmaker": "Pinnacle", "observed_at": "2026-10-30T10:00:00+00:00"}}})
    assert "maçtan 24 saat - 90 dakika önce" in note and "abcdef1234" in note and "1234 maç" in note and "2026-10-30 10:00" in note and "Pinnacle" not in note   # no bookmaker name on the page
    assert "24 h to 90 min before kick-off" in i18n.translate_html(note)


def test_the_smoke_never_records_a_key(monkeypatch):
    import urllib.error

    secret = "SUPERSECRETKEY1234567890"

    def boom(req, timeout=30.0):
        raise urllib.error.URLError(f"failed for {req.full_url}")

    monkeypatch.setattr(smoke.urllib.request, "urlopen", boom)
    rec = smoke.call("x", f"https://example.invalid/v4/sports/?apiKey={secret}", {}, [secret])
    assert rec["status"] == "UNREACHABLE" and secret not in json.dumps(rec) and "***" in rec["message"] and rec["endpoint"] == "https://example.invalid/v4/sports/"


def test_the_frozen_baseline_is_the_plans_measurement_and_verification_catches_a_change():
    m = json.loads((ROOT / "docs" / "phase3" / "s13_baseline_manifest.json").read_text(encoding="utf-8"))
    assert m["n"] == 138 and m["totals"] == {"pick_hit": 94, "ou_hit": 84, "btts_hit": 76, "score_hit": 10} and m["always_over"] == 95 and len(set(m["fixtures"])) == 138
    track = {"generated_at": "t", "matches": [{"key": k, **dict(zip(baseline.FIELDS, o, strict=True))} for k, o in zip(m["fixtures"], m["outcomes"], strict=True)]}
    assert baseline.verify(m, track)["VERIFIED_BASELINE"]
    track["matches"][0] = {**track["matches"][0], "pick_hit": not track["matches"][0]["pick_hit"]}
    bad = baseline.verify(m, track)
    assert not bad["VERIFIED_BASELINE"] and bad["changed"] == [m["fixtures"][0]]
    track["matches"].pop()
    assert baseline.verify(m, track)["missing"] == [m["fixtures"][-1]]


def test_the_smoke_run_reports_missing_keys_and_a_refused_account_without_calling_out(monkeypatch, tmp_path):
    """No network: `call` is replaced; the TEST_ONLY bodies mimic the provider's documented error shape."""
    for env in (smoke.FD_ENV, *smoke.ODDS_ENVS, smoke.AF_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(smoke, "load_dotenv", lambda: None)
    monkeypatch.setenv(smoke.AF_ENV, "af-test-key-value")
    seen = []

    def fake(name, url, headers, secrets, timeout=30.0):
        seen.append(name)
        body = b'{"errors": {"access": "Your account is suspended"}}' if name == "api-football" else b"{}"
        return {"provider": name, "status": "OK", "http": 200, "_body": body}

    monkeypatch.setattr(smoke, "call", fake)
    res = {r["provider"]: r for r in smoke.run()}
    assert res["football-data.org"]["status"] == "NOT_CONFIGURED" and res["the-odds-api (the_odds_api_key)"]["status"] == "NOT_CONFIGURED"
    assert res["api-football"]["status"] == "ACCOUNT_REFUSED" and "suspended" in res["api-football"]["message"] and all("_body" not in r for r in res.values())
    assert res["highlightly"]["status"] == "NOT_CALLED_FROM_THIS_NETWORK" and "espn (keyless)" in seen and "openfootball (keyless)" in seen
    monkeypatch.setattr(smoke, "run", lambda: [{"provider": "x", "status": "OK"}])
    out = tmp_path / "s.json"
    assert smoke.main(["--out", str(out)]) == 0 and json.loads(out.read_text(encoding="utf-8"))["results"][0]["provider"] == "x"


def test_the_inventory_names_its_sources_and_says_unreadable_instead_of_guessing(monkeypatch, tmp_path):
    from src.lineage import inventory

    monkeypatch.setattr(inventory, "_gh", lambda args: None)                                  # gh unavailable
    text = inventory.build(None, None)
    assert "UNREADABLE" in text and "markets_model" not in text and "market_model" in text and "football-data-org" in text
    answers = {"run": '[{"headSha": "abc", "createdAt": "2026-10-30T20:53:26Z", "databaseId": 1, "event": "schedule"}]', "api": '{"sha": "d1", "date": "2026-10-30"}', "secret": "A_KEY\nB_KEY"}
    monkeypatch.setattr(inventory, "_gh", lambda args: answers[args[0]])                     # TEST_ONLY answers in the shape `gh` prints
    smoke_file = tmp_path / "smoke.json"
    smoke_file.write_text(json.dumps({"results": [{"provider": "espn (keyless)", "status": "OK", "http": 200, "latency_ms": 5}]}), encoding="utf-8")
    text = inventory.build(None, smoke_file)
    assert '"headSha": "abc"' in text and "markets-data" in text and "A_KEY, B_KEY" in text and "| espn (keyless) | OK | 200 | 5 |" in text
    out = tmp_path / "inv.md"
    assert inventory.main(["--smoke", str(smoke_file), "--out", str(out)]) == 0 and out.read_text(encoding="utf-8").startswith("# Phase 3")


def test_the_baseline_cli_freezes_and_verifies(tmp_path):
    rows = [{"key": f"K{i:03d}", "date": f"2026-10-{1 + i // 10:02d}", "result": "H", "pick": "H", "pick_hit": i % 2 == 0, "ou_pick": "Üst", "ou_hit": True, "btts_pick": "Var", "btts_hit": False,
             "score_pick": "1-0", "score_hit": False, "score": "1-0", "over_actual": False} for i in range(150)]  # TEST_ONLY  # fmt: skip
    t = tmp_path / "track.json"
    t.write_text(json.dumps({"generated_at": "t", "matches": rows}), encoding="utf-8")
    m = tmp_path / "m.json"
    assert baseline.main(["freeze", "--track", str(t), "--out", str(m)]) == 0
    frozen = json.loads(m.read_text(encoding="utf-8"))
    assert frozen["n"] == 138 and frozen["totals"]["pick_hit"] == 69 and frozen["source_sha256"]
    assert baseline.main(["verify", "--manifest", str(m), "--track", str(t)]) == 0
    rows[0]["score_hit"] = True
    t.write_text(json.dumps({"generated_at": "t2", "matches": rows}), encoding="utf-8")
    assert baseline.main(["verify", "--manifest", str(m), "--track", str(t)]) == 1
