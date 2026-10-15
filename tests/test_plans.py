"""Free / Pro plans (ADR 0062): gates are off by default, the e-mail grant, the stored plan, the old-table migration."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.web import plans
from src.web.store import UserStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("FORECAST_PLANS_ENFORCED", "FORECAST_PRO_EMAILS", "FORECAST_TURSO_URL", "FORECAST_USER_DB"):
        monkeypatch.delenv(name, raising=False)


def test_gates_are_off_by_default_and_on_when_enforced(monkeypatch):
    assert plans.allows("free", "paper_value") is True  # nothing enforced: everyone sees everything
    monkeypatch.setenv("FORECAST_PLANS_ENFORCED", "1")
    assert plans.allows("free", "paper_value") is False
    assert plans.allows("pro", "paper_value") is True
    with pytest.raises(KeyError):
        plans.allows("pro", "no_such_feature")


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


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_USER_DB", str(tmp_path / "users.sqlite"))
    c = TestClient(create_app(ROOT, keys=[]), follow_redirects=False)
    assert c.post("/giris/misafir").status_code == 303
    return c


def test_pages_stay_open_without_enforcement_and_close_for_free_users_with_it(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    for path in ("/tavsiyeler", "/basari-kaydi"):
        assert c.get(path).status_code == 200
    monkeypatch.setenv("FORECAST_PLANS_ENFORCED", "1")
    for path in ("/tavsiyeler", "/basari-kaydi"):
        r = c.get(path)
        assert r.status_code == 403 and "Pro planına ait" in r.text
    assert "Plan: <b>Ücretsiz</b>" in c.get("/profil").text
    assert c.get("/").status_code == 200  # the rest of the app stays open
