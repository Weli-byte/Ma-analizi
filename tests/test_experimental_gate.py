"""Live gate of the experimental goal model (ADR 0073). TEST_ONLY synthetic artifacts and results; not real matches."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.markets.data import StatMatch
from src.markets.experimental import ExperimentalModel
from src.markets.track import experimental_scorecard, load_gate
from src.web import i18n, pages

ROOT = Path(__file__).resolve().parents[1]
GATE_SHA256 = "769349f2a0af3a8c73a1fb9ed69e9e5540f9271466b967dd9f0e65b985576580"
EMPTY = {"corners": None, "yellow_cards": None, "shots_on_target": None}
START = datetime(2026, 8, 1, 15, tzinfo=UTC)
NOW = START + timedelta(days=700)


def test_the_pre_registered_gate_file_is_pinned():
    text = (ROOT / "configs" / "experimental_gate.yaml").read_bytes()
    assert hashlib.sha256(text).hexdigest() == GATE_SHA256, "the gate was edited after registration: that needs a new ADR and a new version, not an edit"
    g = load_gate()
    assert g["version"] == "exp-gate-v1" and g["interim_look_at"] == 100 and g["decision_at"] == 150 and g["targets"] == ["over_2_5", "btts"] and g["_sha256"] == GATE_SHA256


def world(tmp_path, n, ex_quality, with_block=True, cutoff_after_kickoff=False):
    """n fixtures one per 3 days. ex_quality > 0: the experimental probabilities sit on the realised side (better than the flat 0.5 of the current model), < 0 on the wrong side."""
    results = []
    for i in range(n):
        ko = START + timedelta(days=3 * i)
        over = i % 2 == 0
        hg, ag = (2, 2) if over else (1, 0)
        home, away = f"ENG_h{i}", f"ENG_a{i}"
        results.append(StatMatch(f"m{i}", "2026-27", "EPL", ko, ko + timedelta(hours=3), home, away, hg, ag, EMPTY, EMPTY, None))
        p_ex = 0.5 + 0.2 * ex_quality
        yes_over = p_ex if over else 1 - p_ex
        btts_y = (p_ex if (ag > 0) else 1 - p_ex)
        cut = ko + timedelta(hours=1) if cutoff_after_kickoff else ko - timedelta(hours=5)
        art = {"information_cutoff": cut.isoformat(), "generated_at": cut.isoformat(), "league": "EPL",
               "intelligence": {"goals": {"over_under": [{"line": 2.5, "over": 0.5, "under": 0.5}], "btts_yes": 0.5}}}  # fmt: skip
        if with_block:
            art["experimental"] = {"over_under": [{"line": 2.5, "over": yes_over, "under": 1 - yes_over}], "btts_yes": btts_y, "label": "Deneysel model (beta)"}
        d = tmp_path / "artifacts" / "markets" / f"{home}__{away}__{ko.date().isoformat()}"
        d.mkdir(parents=True)
        (d / "intel-x.json").write_text(json.dumps(art), encoding="utf-8")
    return results


def test_clearly_better_after_the_decision_size_is_verified(tmp_path):
    r = experimental_scorecard(tmp_path, world(tmp_path, 160, +1), NOW)
    assert r["matches_paired"] == 160 and r["status"] == "VERIFIED" and r["gate_sha256"] == GATE_SHA256
    for t in ("over_2_5", "btts"):
        assert r["targets"][t]["verdict"] == "EXPERIMENTAL_BETTER" and r["targets"][t]["ci95"][1] < 0 and r["targets"][t]["delta"] < 0


def test_the_same_quality_is_not_verified_before_the_decision_size(tmp_path):
    r = experimental_scorecard(tmp_path, world(tmp_path, 120, +1), NOW)
    assert r["matches_paired"] == 120 and r["status"] == "BETA" and "of 150" in r["status_reason"]


def test_clearly_worse_after_the_interim_look_is_withdrawn(tmp_path):
    r = experimental_scorecard(tmp_path, world(tmp_path, 110, -1), NOW)
    assert r["status"] == "WITHDRAWN" and r["targets"]["over_2_5"]["verdict"] == "EXPERIMENTAL_WORSE"


def test_clearly_worse_before_the_interim_look_is_only_beta(tmp_path):
    r = experimental_scorecard(tmp_path, world(tmp_path, 60, -1), NOW)
    assert r["status"] == "BETA" and r["matches_paired"] == 60          # too early to withdraw: a short record is noise


def test_no_difference_stays_beta_even_with_many_matches(tmp_path):
    r = experimental_scorecard(tmp_path, world(tmp_path, 170, 0), NOW)
    assert r["status"] == "BETA" and r["targets"]["over_2_5"]["verdict"] == "NO_DIFFERENCE_DEMONSTRATED"


def test_only_forecasts_made_before_kickoff_with_a_block_and_a_result_count(tmp_path):
    assert experimental_scorecard(tmp_path, world(tmp_path, 40, +1, cutoff_after_kickoff=True), NOW)["matches_paired"] == 0        # generated after kick-off: not a forecast
    other = tmp_path / "b"
    assert experimental_scorecard(other, world(other, 40, +1, with_block=False), NOW)["matches_paired"] == 0                       # older artifacts without the block
    third = tmp_path / "c"
    res = world(third, 40, +1)
    assert experimental_scorecard(third, res, START + timedelta(days=30))["matches_paired"] < 40                                  # matches that have not kicked off yet are not scored
    assert experimental_scorecard(third, [], NOW)["matches_paired"] == 0                                                           # no result, no row


def test_a_withdrawn_status_stops_new_blocks_and_hides_the_card(tmp_path):
    (tmp_path / "artifacts" / "markets").mkdir(parents=True)
    (tmp_path / "artifacts" / "markets" / "experimental_status.json").write_text(json.dumps({"status": "WITHDRAWN"}), encoding="utf-8")
    model, status = ExperimentalModel.fit([], NOW, root=tmp_path)
    assert model is None and status == "WITHDRAWN (live gate)"
    assert pages.experimental_card(INTEL, {"status": "WITHDRAWN"}) == ""


INTEL = {"home": "Arsenal", "away": "Chelsea", "goals": {"over_under": [{"line": 2.5, "over": 0.55, "under": 0.45}], "btts_yes": 0.5, "btts_no": 0.5},
         "experimental": {"label": "Deneysel model (beta)", "evidence": "kanıt", "over_under": [{"line": 2.5, "over": 0.6, "under": 0.4}], "btts_yes": 0.55, "btts_no": 0.45,
                          "expected_goals": {"home": 1.5, "away": 1.2, "total": 2.7}, "scorelines": [{"score": "1-1", "p": 0.12}]}}  # fmt: skip


def test_the_card_shows_beta_or_verified_with_the_match_count_and_translates(tmp_path):
    beta = pages.experimental_card(INTEL, {"status": "BETA", "matches_paired": 37, "decision_at": 150, "targets": {}})
    assert "Beta:" in beta and "37/150" in beta
    ver = pages.experimental_card(INTEL, {"status": "VERIFIED", "matches_paired": 160, "decision_at": 150, "targets": {"over_2_5": {"delta": -0.03}, "btts": {"delta": -0.02}}})
    assert "Canlı doğrulandı" in ver and "160 maçta" in ver and "-0.030" in ver
    assert "canlı doğrulama henüz başlamadı" in pages.experimental_card(INTEL, None)
    assert "Verified live" in i18n.translate_html(ver)


def test_the_track_page_lists_the_live_record_and_the_rule(tmp_path):
    x = experimental_scorecard(tmp_path, world(tmp_path, 160, +1), NOW)
    html = pages.experimental_track_section(x)
    assert "Deneysel model: canlı kayıt" in html and "Canlı doğrulandı" in html and "160 maç" in html and "kilitlendi" in html
    assert pages.experimental_track_section(None) == ""
    few = pages.experimental_track_section(experimental_scorecard(tmp_path / "none", [], NOW))
    assert "Henüz yeterli maç yok" in few and "Beta (doğrulanıyor)" in few
