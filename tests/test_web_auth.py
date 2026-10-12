"""Session cookie, sign-in providers and logo switch of the mobile web app (ADR 0052)."""

import time

from src.web import auth, session
from src.web.session import User


def test_cookie_round_trips_and_rejects_tampering_expiry_and_garbage():
    token = session.sign(User("google", "Veli", "v@example.com"), now=1000.0)
    assert session.verify(token, now=1001.0) == User("google", "Veli", "v@example.com")
    assert session.verify(token, now=1000.0 + session.MAX_AGE + 1) is None  # expired
    payload, sig = token.rsplit(".", 1)
    assert session.verify(payload + "x." + sig, now=1001.0) is None  # payload changed
    assert session.verify(payload + "." + sig[:-2] + "AA", now=1001.0) is None  # signature changed
    for bad in (None, "", "nodot", "a.b", "....."):
        assert session.verify(bad) is None


def test_a_cookie_signed_with_another_secret_is_refused(monkeypatch):
    monkeypatch.setenv("FORECAST_SESSION_SECRET", "secret-one")
    token = session.sign(User("guest", "Misafir"))
    assert session.verify(token) == User("guest", "Misafir")
    monkeypatch.setenv("FORECAST_SESSION_SECRET", "secret-two")
    assert session.verify(token) is None


GOOD = {"aud": "cid", "iss": "https://accounts.google.com", "sub": "123", "email": "v@example.com", "email_verified": "true", "name": "Veli", "exp": str(int(time.time()) + 600)}  # fmt: skip


def test_google_claims_are_validated_strictly():
    assert auth.validate_google_claims(GOOD, "cid") == User("google", "Veli", "v@example.com", "google:123")
    for bad in (
        {**GOOD, "aud": "other"}, {**GOOD, "iss": "evil.example"}, {**GOOD, "exp": "1"},
        {**GOOD, "email_verified": "false"}, {**GOOD, "sub": ""},
    ):  # fmt: skip
        assert auth.validate_google_claims(bad, "cid") is None
    assert (
        auth.validate_google_claims({**GOOD, "name": ""}, "cid").name == "v"
    )  # falls back to the e-mail name


def test_providers_without_credentials_are_reported_as_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert auth.provider_status() == {"google": False, "apple": False, "phone": False}
    assert auth.verify_google("any-token") is None  # nothing is accepted without a configured client id
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    assert auth.provider_status()["google"] is True


def test_google_endpoint_refuses_an_invalid_credential(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    c = TestClient(create_app(tmp_path, keys=[]))
    r = c.post("/giris/google", json={"credential": "forged"})
    assert r.status_code == 401 and session.COOKIE not in r.cookies
    assert c.post("/giris/google", content=b"not json").status_code == 401


def test_logo_switch_is_off_by_default_and_never_serves_unknown_files(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from src.web.logos import LogoIndex

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    (tmp_path / "artifacts" / "logos").mkdir(parents=True)
    (tmp_path / "artifacts" / "logos" / "league_33973.png").write_bytes(b"\x89PNG")
    monkeypatch.delenv("FORECAST_UI_LOGOS", raising=False)
    off = LogoIndex(tmp_path)
    assert off.league("EPL") is None and off.team_url("ENG_arsenal") is None and off.league_url("EPL") is None
    monkeypatch.setenv("FORECAST_UI_LOGOS", "1")
    on = LogoIndex(tmp_path)
    assert (
        on.league_url("EPL") == "/logo/league/EPL"
        and on.league("NOPE") is None
        and on.team("ENG_nobody") is None
    )
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    c = TestClient(create_app(tmp_path, keys=[]))
    assert c.get("/logo/league/EPL").content == b"\x89PNG" and c.get("/logo/league/NOPE").status_code == 404
    assert c.get("/logo/team/ENG_nobody").status_code == 404


def test_logo_index_maps_clubs_through_the_store_and_the_name_search(tmp_path, monkeypatch):
    """Real Highlightly ids (Manchester United 28867, Tottenham 40781) from the captured match; Burnley via search."""
    import json
    import shutil
    from pathlib import Path

    from src.markets.stats_store import _row, save
    from src.web.logos import LogoIndex

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    page = json.loads(
        (root / "tests/fixtures/real_provider_captures/highlightly/matches_page.json").read_text(
            encoding="utf-8"
        )
    )
    m = page["data"][1]
    save(tmp_path, "EPL", 2026, {m["id"]: _row(m, "EPL", 2026)})
    logos = tmp_path / "artifacts" / "logos"
    logos.mkdir(parents=True)
    for tid in (28867, 40781, 38228):
        (logos / f"{tid}.png").write_bytes(b"\x89PNG")
    (logos / "_search.json").write_text(
        json.dumps({"ENG_burnley": {"id": 38228, "name": "Burnley", "query": "Burnley"}}), encoding="utf-8"
    )
    monkeypatch.setenv("FORECAST_UI_LOGOS", "1")
    idx = LogoIndex(tmp_path)
    assert idx.team_url("ENG_tottenham_hotspur") == "/logo/team/ENG_tottenham_hotspur"
    assert (
        idx.team("ENG_manchester_united").name == "28867.png" and idx.team("ENG_burnley").name == "38228.png"
    )
    assert idx.team("ENG_arsenal") is None  # never listed: no logo, the page shows initials
