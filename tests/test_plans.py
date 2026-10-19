"""Free / Pro plans (ADR 0062): gates are off by default, the e-mail grant, the stored plan, the old-table migration."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.web import pages, plans
from src.web.store import UserStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("FORECAST_PLANS_ENFORCED", "FORECAST_PRO_EMAILS", "FORECAST_TURSO_URL", "FORECAST_USER_DB"):
        monkeypatch.delenv(name, raising=False)


def test_gates_are_off_by_default_and_on_when_enforced(monkeypatch):
    assert plans.allows("free", "goal_markets") is True  # nothing enforced: everyone sees everything
    assert plans.locked_tabs("free") == set()
    monkeypatch.setenv("FORECAST_PLANS_ENFORCED", "1")
    assert plans.allows("free", "goal_markets") is False and plans.allows("pro", "goal_markets") is True
    assert plans.locked_tabs("free") == {"market", "hakem", "oyuncu"} and plans.locked_tabs("pro") == set()
    with pytest.raises(KeyError):
        plans.allows("pro", "no_such_feature")


def test_the_owner_contact_address_is_always_pro():
    from src.web.brand import CONTACT_EMAIL

    assert plans.effective_plan(CONTACT_EMAIL, "free") == "pro"
    assert plans.effective_plan(CONTACT_EMAIL.upper(), "free") == "pro"


def test_pro_email_list_grants_pro_case_insensitively_and_only_with_an_email(monkeypatch):
    monkeypatch.setenv("FORECAST_PRO_EMAILS", " Owner@Example.com , other@example.com ")
    assert plans.effective_plan("owner@example.com", "free") == "pro"
    assert plans.effective_plan("stranger@example.com", "free") == "free"
    assert plans.effective_plan(None, "free") == "free"  # a guest or phone user has no verified e-mail
    assert plans.effective_plan(None, "pro") == "pro"  # a stored plan still counts
    assert plans.effective_plan(None, "garbage") == "free"


def test_store_keeps_the_plan_and_touch_does_not_reset_it(tmp_path):
    store = UserStore(tmp_path)
    assert store.plan("nobody") == "free"
    store.touch("u1", "google", "Ada", "ada@example.com")
    assert store.plan("u1") == "free"
    store.set_plan("u1", "pro")
    store.touch("u1", "google", "Ada", "ada@example.com")  # a later sign-in
    assert store.plan("u1") == "pro"
    with pytest.raises(ValueError):
        store.set_plan("u1", "gold")


def test_a_users_table_created_before_plans_is_migrated_without_losing_rows(tmp_path):
    path = tmp_path / "artifacts" / "users.sqlite"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE users(uid TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL, email TEXT, "
            "created_at TEXT NOT NULL, last_seen TEXT NOT NULL)"
        )
        db.execute("INSERT INTO users VALUES('u1','google','Ada','a@x.com','t','t')")
    db.close()
    store = UserStore(tmp_path)
    assert store.plan("u1") == "free"
    store.set_plan("u1", "pro")
    assert UserStore(tmp_path).plan("u1") == "pro"  # a second start does not try to add the column again


def test_locked_tabs_show_a_pro_notice_instead_of_their_content():
    from tests.test_match_details import Logos, _intel

    open_page = pages.match_page(_intel(None), [], Logos())
    assert "Pro planına ait" not in open_page and "(Pro)" not in open_page
    shut = pages.match_page(
        _intel(None), [], Logos(), locked=plans.TAB_FEATURES.keys() & {"market", "hakem", "oyuncu"}
    )
    assert shut.count("Bu sekme Pro planına ait.") == 3 and shut.count("(Pro)</button>") == 3
    assert "<section id='t-ozet'" in shut and "t-form" in shut  # the other tabs are untouched


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_USER_DB", str(tmp_path / "users.sqlite"))
    c = TestClient(create_app(ROOT, keys=[]), follow_redirects=False)
    assert c.post("/giris/misafir").status_code == 303
    return c


def test_tips_and_track_record_stay_open_even_when_plans_are_enforced(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("FORECAST_PLANS_ENFORCED", "1")
    for path in ("/tavsiyeler", "/basari-kaydi", "/"):
        assert c.get(path).status_code == 200
    assert "Plan: <b>Ücretsiz</b>" in c.get("/profil").text


def test_free_users_do_not_see_over_under_btts_or_count_tips_when_gates_are_on(monkeypatch):
    from tests.test_match_details import Logos, _intel

    intel = _intel(None)
    intel["tips"] = [
        {"market": "match_result", "pick": "Home win", "probability": 0.5, "lean": "lean"},
        {"market": "goals_2_5", "pick": "Over 2.5 goals", "probability": 0.55, "lean": "lean"},
        {"market": "btts", "pick": "Both teams score", "probability": 0.5, "lean": "toss-up"},
    ]
    full = pages.match_page(intel, [], Logos())
    assert "2.5 üst (3+ gol)" in full and "İki takım da gol atar</td>" in full
    shut = pages.match_page(intel, [], Logos(), locked={"market"})
    assert "2.5 üst (3+ gol)" not in shut and "İki takım da gol atar</td>" not in shut
    assert "Pro planına ait" in shut and "Arsenal kazanır" in shut  # the 1X2 tip stays
    assert "İki takım da gol atar:" not in shut  # the summary's over/under + BTTS line is gone too


def test_tips_list_keeps_only_free_markets_for_a_free_user(monkeypatch):
    tips = [{"market": m} for m in ("match_result", "double_chance", "goals_2_5", "btts", "corners_9_5")]
    assert plans.filter_tips(tips, "free") == tips  # gates off: everything
    monkeypatch.setenv("FORECAST_PLANS_ENFORCED", "1")
    assert [t["market"] for t in plans.filter_tips(tips, "free")] == ["match_result", "double_chance"]
    assert plans.filter_tips(tips, "pro") == tips
