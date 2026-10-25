"""The live records of the models under test on the public track page, end to end (ADR 0073, 0076). TEST_ONLY synthetic files; not real matches."""

import importlib.util
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from tests.test_shadow_gate import NOW, world

ROOT = Path(__file__).resolve().parents[1]


def gate_json(n=160, quality=+1):
    from src.league_models.rollout import gate as gate_mod

    return gate_mod.evaluate(*world(n, quality), NOW)


def experimental_json():
    return {"status": "BETA", "status_reason": "12 of 150 matches; no clear difference demonstrated yet", "matches_paired": 12, "interim_look_at": 100, "decision_at": 150,
            "targets": {"over_2_5": {"n": 12, "log_loss_experimental": 0.66, "log_loss_current": 0.67, "delta": -0.01, "ci95": [-0.05, 0.03], "verdict": "NO_DIFFERENCE_DEMONSTRATED"},
                        "btts": {"n": 12, "verdict": "TOO_FEW_MATCHES"}}}  # fmt: skip


def app_client(tmp_path, track, gate=None):
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    d = tmp_path / "artifacts" / "markets"
    d.mkdir(parents=True)
    (d / "track_record.json").write_text(json.dumps(track), encoding="utf-8")
    if gate is not None:
        (d / "shadow_gate.json").write_text(json.dumps(gate), encoding="utf-8")
    c = TestClient(create_app(tmp_path, keys=["k"], now_fn=lambda: datetime(2026, 10, 25, tzinfo=UTC)))
    c.post("/giris/misafir")
    return c


def test_the_track_page_shows_the_card_record_and_the_shadow_record_even_before_the_first_scored_match(tmp_path):
    c = app_client(tmp_path, {"matches_scored": 0, "experimental": experimental_json()}, gate_json())
    html = c.get("/basari-kaydi").text
    assert "Henüz sonuçlanmış maç yok" in html
    assert "Deneysel model: canlı kayıt" in html and "Beta (doğrulanıyor)" in html and "12 maç" in html
    assert "Gölge modeller: canlı kayıt" in html and "Aday (karar sahibinde)" in html and "Her ligi ayrı eğitilmiş model" in html and "kilitlendi" in html
    assert "Hiyerarşik model" in html and "Gölgede (kanıt toplanıyor)" in html                          # the second family has no records yet: shown, in the shadow


def test_the_english_page_translates_both_sections(tmp_path):
    c = app_client(tmp_path, {"matches_scored": 0, "experimental": experimental_json()}, gate_json())
    c.cookies.set("lang", "en")
    html = c.get("/basari-kaydi").text
    assert "Shadow models: live record" in html and "Experimental model: live record" in html and "Candidate (the owner decides)" in html and "Gölge modeller" not in html


def test_without_a_shadow_verdict_the_page_simply_has_no_such_section(tmp_path):
    c = app_client(tmp_path, {"matches_scored": 0})
    html = c.get("/basari-kaydi").text
    assert "Henüz sonuçlanmış maç yok" in html and "Gölge modeller" not in html and "Deneysel model: canlı kayıt" not in html


def test_the_api_serves_the_shadow_verdict_inside_the_track_record(tmp_path):
    c = app_client(tmp_path, {"matches_scored": 0}, gate_json())
    r = c.get("/v1/track-record", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.json()["data"]["shadow_gate"]["families"]["P_dc_sep"]["status"] == "PROMOTION_CANDIDATE"


def test_the_vercel_bundle_carries_the_shadow_verdict(tmp_path):
    spec = importlib.util.spec_from_file_location("mvb2", ROOT / "scripts" / "make_vercel_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = tmp_path / "p"
    (fake / "artifacts" / "markets").mkdir(parents=True)
    for name in ("track_record.json", "shadow_gate.json"):
        (fake / "artifacts" / "markets" / name).write_text("{}", encoding="utf-8")
    for d in ("src", "configs"):
        (fake / d).mkdir()
        (fake / d / "x").write_text("", encoding="utf-8")
    (fake / "requirements-api.lock").write_text("a==1\n", encoding="utf-8")
    files = mod.build(tmp_path / "out", fake)
    assert "artifacts/markets/shadow_gate.json" in files and "artifacts/markets/track_record.json" in files


def test_an_interval_from_a_handful_of_weeks_is_not_printed_and_the_status_text_is_turkish():
    from src.web import i18n, pages

    few = experimental_json()
    few["targets"]["over_2_5"]["blocks"] = 1
    html = pages.experimental_track_section(few)
    assert "az hafta var" in html and "-0.05" not in html and "belirgin bir fark henüz kanıtlanmadı" in html and "no clear difference demonstrated" not in html
    many = experimental_json()
    many["targets"]["over_2_5"]["blocks"] = 9
    assert "-0.0500 / +0.0300" in pages.experimental_track_section(many)
    ver = {**experimental_json(), "status": "VERIFIED", "matches_paired": 160}
    assert "160 maçta mevcut modelden belirgin biçimde iyi bulundu" in pages.experimental_track_section(ver)
    assert "clearly better than the current model" in i18n.translate_html(pages.experimental_track_section(ver))


def test_the_shadow_section_orders_the_rule_markets_first_and_says_so_when_nothing_is_paired_yet():
    from src.web import pages

    html = pages.shadow_gate_section(gate_json())
    assert html.index("2.5 alt/üst") < html.index("Karşılıklı gol") < html.index("Maç sonucu (bilgi)")
    empty = pages.shadow_gate_section(gate_json(n=0))
    assert empty.count("Henüz eşleşmiş (sonuçlanmış) maç yok.") == 2 and "<table>" not in empty
