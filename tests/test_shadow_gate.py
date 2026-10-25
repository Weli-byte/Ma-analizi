"""Pre-registered live gate of the exploratory shadow families (ADR 0076). TEST_ONLY synthetic records and outcomes; not real matches."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.league_models.rollout import gate as gate_mod
from src.league_models.rollout.__main__ import main
from src.web import i18n, pages

ROOT = Path(__file__).resolve().parents[1]
GATE_SHA256 = "9bd999e4301557ff071ffe5c85bd9b2430e23a95e1241229b5c7338fb3cd7cc4"
START = datetime(2026, 8, 1, 15, tzinfo=UTC)
NOW = START + timedelta(days=700)


def test_the_pre_registered_shadow_gate_file_is_pinned():
    text = (ROOT / "configs" / "league_models" / "shadow_gate.yaml").read_bytes()
    assert hashlib.sha256(text).hexdigest() == GATE_SHA256, "the gate was edited after registration: that needs a new ADR and a new version, not an edit"
    g = gate_mod.load_gate()
    assert g["version"] == "shadow-gate-v1" and g["stage"] == "t-24h" and g["families"] == ["P_dc_sep", "H_dc_hier"] and g["rule_targets"] == ["over_under", "btts"] and g["information_targets"] == ["1x2"]
    assert (g["interim_look_at"], g["decision_at"]) == (100, 150) and g["_sha256"] == GATE_SHA256


def rec(fid, kick, family, stage="t-24h", p_over=None, p_btts=None, p1=None, version="v1"):
    return {"fixture_id": fid, "league_id": "ENG1", "kickoff_utc": kick.isoformat(), "stage": stage, "model_version": version, "family": family, "role": "global" if family == "global" else "candidate",
            "p_home": None if p1 is None else p1[0], "p_draw": None if p1 is None else p1[1], "p_away": None if p1 is None else p1[2], "p_over25": p_over, "p_btts_yes": p_btts}  # fmt: skip


def world(n, quality, stage="t-24h", only=("P_dc_sep",)):
    """n fixtures, one every 3 days. quality +1: the family sits on the realised side (better than the flat 0.5 of the global record); -1: on the wrong side; 0: the same as global."""
    records, outcomes = [], {}
    for i in range(n):
        kick = START + timedelta(days=3 * i)
        over = i % 2 == 0
        hg, ag = (2, 2) if over else (1, 0)
        fid = f"fdorg-{i}"
        outcomes[fid] = {"fixture_id": fid, "home_goals": hg, "away_goals": ag}
        records.append(rec(fid, kick, "global", stage, p_over=0.5, p_btts=0.5, p1=(0.4, 0.3, 0.3)))
        p = 0.5 + 0.2 * quality
        for fam in only:
            records.append(rec(fid, kick, f"{fam}:over_under", stage, p_over=p if over else 1 - p))
            records.append(rec(fid, kick, f"{fam}:btts", stage, p_btts=p if ag > 0 else 1 - p))
            records.append(rec(fid, kick, f"{fam}:1x2", stage, p1=(0.4, 0.3, 0.3)))
    return records, outcomes


def test_clearly_better_on_both_rule_targets_after_the_decision_size_is_a_promotion_candidate():
    r = gate_mod.evaluate(*world(160, +1), NOW)
    f = r["families"]["P_dc_sep"]
    assert f["status"] == "PROMOTION_CANDIDATE" and f["resolved_pairs_min_over_rule_targets"] == 160 and "nothing is routed" in f["status_reason"]
    assert all(f["targets"][t]["verdict"] == "FAMILY_BETTER" and f["targets"][t]["ci95"][1] < 0 for t in ("over_under", "btts"))
    assert f["targets"]["1x2"]["rule_target"] is False and r["families"]["H_dc_hier"]["status"] == "SHADOW"          # a family without records stays in the shadow


def test_better_but_not_enough_matches_clearly_worse_after_the_interim_look_and_noise():
    assert gate_mod.evaluate(*world(120, +1), NOW)["families"]["P_dc_sep"]["status"] == "SHADOW"
    assert gate_mod.evaluate(*world(110, -1), NOW)["families"]["P_dc_sep"]["status"] == "RETIRE"
    assert gate_mod.evaluate(*world(60, -1), NOW)["families"]["P_dc_sep"]["status"] == "SHADOW"          # too early to retire: a short record is noise
    assert gate_mod.evaluate(*world(170, 0), NOW)["families"]["P_dc_sep"]["status"] == "SHADOW"


def test_only_the_t_24h_snapshot_counts_and_the_information_target_never_decides():
    assert gate_mod.evaluate(*world(160, +1, stage="t-90m"), NOW)["families"]["P_dc_sep"]["resolved_pairs_min_over_rule_targets"] == 0        # another stage is not counted (no double counting)
    recs, outs = world(160, +1)
    recs = [r for r in recs if not r["family"].endswith(":btts")]                                          # one rule target has no records at all
    f = gate_mod.evaluate(recs, outs, NOW)["families"]["P_dc_sep"]
    assert f["status"] == "SHADOW" and f["targets"]["btts"]["verdict"] == "TOO_FEW_PAIRS"


def test_the_gate_command_writes_the_verdict_next_to_the_stores_and_the_prepare_command_reports_the_history(tmp_path, monkeypatch, capsys):
    d = tmp_path / "artifacts" / "league_models" / "rollout"
    d.mkdir(parents=True)
    recs, outs = world(30, +1)
    (d / "prospective.jsonl").write_text("\n".join(json.dumps({**r, "content_hash": "x"}) for r in recs) + "\n", encoding="utf-8")
    (d / "outcomes.jsonl").write_text("\n".join(json.dumps(o) for o in outs.values()) + "\n", encoding="utf-8")
    assert main(["gate", "--root", str(tmp_path), "--now", "2027-01-01T00:00:00Z"]) == 0
    res = json.loads((d / "shadow_gate.json").read_text(encoding="utf-8"))
    assert res["gate_sha256"] == GATE_SHA256 and res["families"]["P_dc_sep"]["targets"]["over_under"]["resolved_pairs"] == 30
    assert json.loads(capsys.readouterr().out)["P_dc_sep"]["status"] == "SHADOW"

    class World:
        history = [1, 2, 3]
        stale_files = []
        missing_files = ["x"]

    import src.markets.of_source as of_source

    monkeypatch.setattr(of_source, "load_world", lambda *a, **k: World())
    assert main(["prepare", "--root", str(ROOT), "--now", "2027-01-01T00:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["history_matches"] == 3


def test_the_track_page_section_shows_the_numbers_the_rule_and_that_nothing_is_routed():
    res = gate_mod.evaluate(*world(160, +1), NOW)
    html = pages.shadow_gate_section(res)
    assert "Gölge modeller: canlı kayıt" in html and "Aday (karar sahibinde)" in html and "kilitlendi" in html and "hiçbir şeyi otomatik değiştirmez" in html and "160" in html
    assert pages.shadow_gate_section(None) == ""
    assert "Shadow models: live record" in i18n.translate_html(html) and "Candidate (the owner decides)" in i18n.translate_html(html)


def test_the_api_track_record_carries_the_shadow_verdict_when_the_file_exists(tmp_path):
    from src.api.service import ForecastService

    d = tmp_path / "artifacts" / "markets"
    d.mkdir(parents=True)
    (d / "track_record.json").write_text(json.dumps({"matches_scored": 0}), encoding="utf-8")
    svc = ForecastService(tmp_path, lambda: NOW)
    assert "shadow_gate" not in svc.track_record()
    (d / "shadow_gate.json").write_text(json.dumps({"families": {}}), encoding="utf-8")
    assert svc.track_record()["shadow_gate"] == {"families": {}}


def test_the_workflow_restores_publishes_and_never_holds_a_secret():
    wf = (ROOT / ".github" / "workflows" / "shadow-cloud.yml").read_text(encoding="utf-8")
    assert "shadow-data" in wf and "rollout prepare" in wf and "for cmd in shadow reconcile monitor scorecard gate" in wf and "concurrency:" in wf
    assert "secrets.FOOTBALL_DATA_ORG_API_KEY" in wf and "secrets.DATA_REPO_DEPLOY_KEY" in wf and "AIza" not in wf and "sk-" not in wf
