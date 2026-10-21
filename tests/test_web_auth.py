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
    flipped = ("B" if sig[0] != "B" else "C") + sig[
        1:
    ]  # first char: the last base64 chars carry unused bits (flaky ~1/1000)
    assert session.verify(payload + "." + flipped, now=1001.0) is None  # signature changed
    for bad in (None, "", "nodot", "a.b", "....."):
        assert session.verify(bad) is None


def test_a_cookie_signed_with_another_secret_is_refused(monkeypatch):
    monkeypatch.setenv("FORECAST_SESSION_SECRET", "secret-one")
    token = session.sign(User("guest", "Misafir"))
    assert session.verify(token) == User("guest", "Misafir")
    monkeypatch.setenv("FORECAST_SESSION_SECRET", "secret-two")
    assert session.verify(token) is None


GOOD = {"aud": "cid", "iss": "https://accounts.google.com", "sub": "123", "email": "v@example.com", "email_verified": "true", "name": "Veli", "exp": str(int(time.time()) + 86400)}  # fmt: skip


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


def test_user_store_keeps_favourites_per_user_and_deletes_everything(tmp_path):
    from src.web.store import UserStore

    st = UserStore(tmp_path)
    st.touch("guest:a", "guest", "Misafir", None)
    st.touch("google:1", "google", "Veli", "v@example.com")
    assert st.toggle("guest:a", "ENG_arsenal") is True and st.toggle("guest:a", "ESP_barcelona") is True
    assert st.favorites("guest:a") == ["ENG_arsenal", "ESP_barcelona"] and st.favorites("google:1") == []
    assert st.toggle("guest:a", "ENG_arsenal") is False and st.favorites("guest:a") == ["ESP_barcelona"]
    st.touch("google:1", "google", "Veli K", "v@example.com")  # a second sign-in updates, never duplicates
    st.delete_user("guest:a")
    assert st.favorites("guest:a") == []


def test_favourites_search_account_deletion_and_summary_routes(tmp_path):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from src.api.app import create_app

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    c = TestClient(create_app(tmp_path, keys=[]), follow_redirects=False)
    assert c.get("/favoriler").status_code == 303 and c.post("/favori/ENG_arsenal").status_code == 303
    assert c.get("/ara?q=x").status_code == 303 and c.post("/hesabi-sil").status_code == 303
    assert "Proje özeti" in c.get("/sunum").text  # the summary is public
    c.post("/giris/misafir")
    assert "Henüz favori takımın yok" in c.get("/favoriler").text
    r = c.post("/favori/ENG_arsenal?next=/mac/x")
    assert r.status_code == 303 and r.headers["location"] == "/mac/x"
    assert (
        c.post("/favori/ENG_arsenal?next=//evil.example").headers["location"] == "/favoriler"
    )  # no open redirect
    c.post("/favori/ENG_arsenal")  # the second toggle above removed it: add it again
    assert "Arsenal" in c.get("/favoriler").text
    assert c.post("/favori/x").status_code == 303  # an id without an underscore is ignored
    assert "bulunamadı" in c.get("/ara?q=zzzz").text and "Bir takım adı yaz" in c.get("/ara").text
    assert c.get("/ara?q=" + "a" * 100).status_code == 200
    r = c.post("/hesabi-sil")
    assert r.status_code == 303 and r.headers["location"] == "/giris"
    assert c.get("/profil").status_code in (200, 303)


def test_form_and_goal_charts_render_and_missing_data_is_skipped():
    from src.web import pages

    form = {
        "home": [{"date": "2026-10-04", "opponent": "Leeds", "home": True, "score": "2-0", "result": "W"}],
        "away": [],
    }
    html = pages.form_section({"home": "Arsenal", "away": "Leeds", "form": form})
    assert "Son 5 maç" in html and "Leeds" in html and "veri yok" in html and ">G<" in html
    assert pages.form_section({"home": "A", "away": "B", "form": None}) == ""
    assert "Toplam gol dağılımı" in pages.goals_chart(
        {"total_goals_distribution": [0.1, 0.2, 0.3, 0.2, 0.1, 0.05, 0.03, 0.02]}
    )
    assert pages.goals_chart({}) == ""


def test_english_translation_touches_text_only_and_keeps_names_urls_and_scripts():
    from src.web.i18n import translate_html

    html = (
        "<html lang='tr'><head><style>.Ligler{color:red}</style></head><body><a href='/ligler?x=Ligler' title='Ligler'>Ligler</a>"
        "<p>Atalanta kazanır ya da berabere %85</p><p>12 Eki 19:00 (TSİ)</p><p>Kasımpaşa - Altay</p><p>Bu hafta 5 maç için tahminimiz var.</p>"
        "<script>var Ligler='Ligler';</script></body></html>"
    )
    out = translate_html(html)
    assert (
        "<html lang='en'>" in out and "<a href='/ligler?x=Ligler' title='Ligler'>Leagues</a>" in out
    )  # attributes untouched
    assert "Atalanta to win or draw 85%" in out and "12 Oct 19:00 (Turkey time)" in out
    assert "Kasımpaşa - Altay" in out  # club names that contain month/word fragments stay intact
    assert "5 matches have predictions" in out
    assert ".Ligler{color:red}" in out and "var Ligler='Ligler';" in out  # style and script copied unchanged


def test_language_switch_cookie_translates_pages_but_never_the_json_api(tmp_path):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from src.api.app import create_app

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    c = TestClient(create_app(tmp_path, keys=["k"]), follow_redirects=False)
    assert "Misafir olarak devam et" in c.get("/giris").text
    r = c.get("/dil/en", headers={"referer": "http://testserver/giris"})
    assert r.status_code == 303 and r.headers["location"] == "/giris" and "lang=en" in r.headers["set-cookie"]
    c.cookies.set("lang", "en")
    page = c.get("/giris")
    assert "Continue as guest" in page.text and "Misafir olarak devam et" not in page.text
    assert (
        c.get("/dil/tr", headers={"referer": "//evil.example/x"}).headers["location"] == "/x"
    )  # no open redirect
    assert c.get("/v1/health").json()["data"]["status"] == "ok"  # the API is never translated
    c.cookies.set("lang", "tr")
    assert "Misafir olarak devam et" in c.get("/giris").text


def test_phone_numbers_are_normalised_masked_hashed_and_throttled():
    from src.web import phone

    assert (
        phone.normalize("0532 123 45 67") == "+905321234567"
        and phone.normalize("532-123-45-67") == "+905321234567"
    )
    assert (
        phone.normalize("+44 7700 900123") == "+447700900123"
        and phone.normalize("0044 7700 900123") == "+447700900123"
    )
    for bad in ("", "abc", "123", "+0123456789", "+1", "05321234"):
        assert phone.normalize(bad) is None
    u = phone.user_for("+905321234567")
    assert u.name == "Telefon ····4567" and u.uid.startswith("phone:") and "5321234567" not in u.uid + u.name
    clock = [0.0]
    t = phone.Throttle(2, 60, lambda: clock[0])
    assert t.allow("a") and t.allow("a") and not t.allow("a") and t.allow("b")
    clock[0] = 61
    assert t.allow("a")  # the window passed


def test_phone_sign_in_is_off_without_credentials_and_guards_the_flow_when_on(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from src.api.app import create_app
    from src.web import phone

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(create_app(tmp_path, keys=[]), follow_redirects=False)
    assert phone.configured() is False and c.get("/giris/telefon").status_code == 503
    assert c.post("/giris/telefon/kod", data={"phone": "+905321234567"}).status_code == 503
    assert c.post("/giris/telefon/dogrula", data={"code": "123456"}).headers["location"] == "/giris/telefon"
    assert phone.send_code("+905321234567") is False and phone.check_code("+905321234567", "123456") is False
    # configured: the flow logic (the SMS call itself is replaced; the live Twilio call is NOT verified)
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"):
        monkeypatch.setenv(k, "x")
    sent: list[str] = []
    monkeypatch.setattr(phone, "send_code", lambda n: (sent.append(n), True)[1])
    monkeypatch.setattr(phone, "check_code", lambda n, code: code == "123456")
    assert c.get("/giris/telefon").status_code == 200
    assert c.post("/giris/telefon/kod", data={"phone": "abc"}).status_code == 400
    ok = c.post("/giris/telefon/kod", data={"phone": "0532 123 45 67"})
    assert (
        ok.status_code == 200
        and "····4567" in ok.text
        and sent == ["+905321234567"]
        and "mt_phone" in ok.headers["set-cookie"]
    )
    assert c.post("/giris/telefon/dogrula", data={"code": "000000"}).status_code == 400
    done = c.post("/giris/telefon/dogrula", data={"code": "123456"})
    assert (
        done.status_code == 303
        and done.headers["location"] == "/"
        and session.COOKIE in done.headers["set-cookie"]
    )
    c.cookies.set(session.COOKIE, done.cookies.get(session.COOKIE) or "")
    for _ in range(3):
        c.post("/giris/telefon/kod", data={"phone": "0532 123 45 67"})
    assert c.post("/giris/telefon/kod", data={"phone": "0532 123 45 67"}).status_code == 429  # throttled


def test_cookies_are_marked_secure_only_behind_https(tmp_path):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from src.api.app import create_app

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    c = TestClient(create_app(tmp_path, keys=[]), follow_redirects=False)
    assert "secure" not in c.post("/giris/misafir").headers["set-cookie"].lower()
    https = c.post("/giris/misafir", headers={"x-forwarded-proto": "https"})
    assert "Secure" in https.headers["set-cookie"] and "HttpOnly" in https.headers["set-cookie"]


def test_space_bundle_has_the_image_recipe_and_no_secrets_or_local_data(tmp_path):
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.make_space_bundle import build

    files = build(tmp_path / "space")
    for needed in (
        "Dockerfile",
        "space_start.sh",
        "README.md",
        "requirements-api.lock",
        "src/api/app.py",
        "configs/markets.yaml",
    ):
        assert needed in files
    assert not any(f.startswith(("artifacts/", ".env")) or f.endswith((".sqlite", ".pem")) for f in files)
    assert "sdk: docker" in (tmp_path / "space" / "README.md").read_text(encoding="utf-8")
    start = (tmp_path / "space" / "space_start.sh").read_text(encoding="utf-8")
    assert (
        "DATA_REPO_DEPLOY_KEY" in start and "GITHUB_ED25519" in start and "StrictHostKeyChecking yes" in start
    )


def test_login_uses_googles_own_button_only_when_a_client_id_is_configured(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from src.api.app import create_app

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "configs", tmp_path / "configs")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    c = TestClient(create_app(tmp_path, keys=[]))
    off = c.get("/giris").text
    assert "g_id_signin" not in off and "pick('google')" in off and c.get("/favicon.ico").status_code == 200
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    on = c.get("/giris").text
    assert (
        "g_id_signin" in on
        and "data-ux_mode='popup'" in on
        and "data-client_id='cid.apps.googleusercontent.com'" in on
    )
    assert "accounts.google.com/gsi/client" in on and "Google girişi doğrulanamadı (sunucu" in on
