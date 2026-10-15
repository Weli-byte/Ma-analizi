"""S19 preparation (ADR 0057): public legal pages, persistent user DB path, Vercel bundle."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.web import pages
from src.web.i18n import translate_html
from src.web.store import UserStore

ROOT = Path(__file__).resolve().parents[1]


def test_legal_pages_are_public_complete_and_translated(tmp_path):
    c = TestClient(create_app(tmp_path, keys=[]), follow_redirects=False)  # no session at all
    for path, needle in (("/gizlilik", "Gizlilik politikası"), ("/kosullar", "Kullanım koşulları")):
        r = c.get(path)
        assert r.status_code == 200 and needle in r.text and "taslak" in r.text, path
    privacy = pages.legal_page("privacy")
    assert "Misafir girişi" in privacy and "Verilerimi sil" in privacy
    en = translate_html(privacy)
    assert "Privacy policy" in en and "Your password never reaches us" in en
    assert (
        "Gizlilik" not in translate_html(pages.legal_page("terms")).split("<title>")[1].split("</title>")[0]
    )
    login = c.get("/giris").text
    assert "/gizlilik" in login and "/kosullar" in login


def test_user_db_can_live_on_a_persistent_path(tmp_path, monkeypatch):
    target = tmp_path / "volume" / "users.sqlite"
    target.parent.mkdir()
    monkeypatch.setenv("FORECAST_USER_DB", str(target))
    store = UserStore(tmp_path / "elsewhere")
    store.touch("u1", "guest", "Misafir", None)
    assert target.exists() and not (tmp_path / "elsewhere" / "artifacts" / "users.sqlite").exists()


def test_vercel_bundle_has_the_entry_point_the_newest_data_and_no_secrets(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_vercel_bundle", ROOT / "scripts" / "make_vercel_bundle.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = tmp_path / "proj"
    (fake / "artifacts" / "markets" / "A__B__2026-10-14").mkdir(parents=True)
    for stamp in ("1", "2"):
        (fake / "artifacts" / "markets" / "A__B__2026-10-14" / f"intel-{stamp}.json").write_text(
            stamp, encoding="utf-8"
        )
    (fake / "artifacts" / "markets" / "track_record.json").write_text("{}", encoding="utf-8")
    (fake / "artifacts" / "injuries" / "2026-10-14").mkdir(parents=True)
    (fake / "artifacts" / "injuries" / "2026-10-14" / "inj-1.json").write_text("{}", encoding="utf-8")
    (fake / ".env").write_text("SECRET=1", encoding="utf-8")
    (fake / "src").mkdir()
    (fake / "src" / "x.py").write_text("", encoding="utf-8")
    (fake / "configs").mkdir()
    (fake / "configs" / "c.yaml").write_text("", encoding="utf-8")
    (fake / "requirements-api.lock").write_text("fastapi==1\n", encoding="utf-8")
    files = mod.build(tmp_path / "out", fake)
    assert "index.py" in files and "vercel.json" not in files and "requirements.txt" in files
    assert "artifacts/markets/A__B__2026-10-14/intel-2.json" in files  # only the newest forecast of a fixture
    assert "artifacts/markets/A__B__2026-10-14/intel-1.json" not in files
    assert (
        "artifacts/injuries/2026-10-14/inj-1.json" in files and "artifacts/markets/track_record.json" in files
    )
    assert not any(f.startswith(".env") or "users.sqlite" in f for f in files)
    entry = (tmp_path / "out" / "index.py").read_text()
    assert "/tmp/users.sqlite" in entry and "app = create_app(ROOT)" in entry
    assert mod.main(["--out", str(tmp_path / "real")]) == 0


# ---------------------------------------------------------------------------------------- Sign in with Apple (ADR 0057)
def _apple_fixture(claims_override=None, kid="k1"):
    """A locally generated RSA key pair + a token signed like Apple's. NOT Apple's real keys (not verified live)."""
    import base64
    import time

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": kid, "n": b64(pub.n.to_bytes(256, "big")), "e": b64(pub.e.to_bytes(3, "big"))}
    claims = {"iss": "https://appleid.apple.com", "aud": "com.example.web", "sub": "001234.abc", "exp": int(time.time()) + 600, "email": "anna@privaterelay.appleid.com"}  # fmt: skip
    claims.update(claims_override or {})
    head = b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    body = b64(json.dumps(claims).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(sig)}", [jwk]


def test_apple_token_is_verified_by_signature_issuer_audience_and_expiry():
    from src.web import auth

    token, keys = _apple_fixture()
    user = auth.validate_apple_token(token, "com.example.web", keys)
    assert (
        user
        and user.provider == "apple"
        and user.uid == "apple:001234.abc"
        and user.email.startswith("anna@")
    )
    head, body, sig = token.split(".")
    assert (
        auth.validate_apple_token(f"{head}.{body}x.{sig}", "com.example.web", keys) is None
    )  # tampered payload
    assert auth.validate_apple_token(token, "com.other.app", keys) is None  # wrong audience
    assert (
        auth.validate_apple_token(token, "com.example.web", [{**keys[0], "kid": "other"}]) is None
    )  # unknown key id
    other_token, other_keys = _apple_fixture()
    assert (
        auth.validate_apple_token(other_token, "com.example.web", keys) is None
    )  # signed by a different key
    for bad in ({"iss": "https://evil.example"}, {"exp": 1}, {"sub": ""}):
        t, k = _apple_fixture(bad)
        assert auth.validate_apple_token(t, "com.example.web", k) is None, bad
    assert auth.validate_apple_token("not.a.jwt", "com.example.web", keys) is None
    assert auth.validate_apple_token("garbage", "com.example.web", keys) is None


def test_apple_route_and_button_follow_the_configuration(tmp_path, monkeypatch):
    from src.web import auth

    token, keys = _apple_fixture()
    monkeypatch.setattr(auth, "apple_keys", lambda now=None: keys)
    app = create_app(tmp_path, keys=[])
    c = TestClient(app, follow_redirects=False)
    monkeypatch.delenv("APPLE_CLIENT_ID", raising=False)
    assert auth.provider_status()["apple"] is False and "appleid.auth.js" not in c.get("/giris").text
    assert c.post("/giris/apple", json={"id_token": token}).status_code == 401  # not configured: refused
    monkeypatch.setenv("APPLE_CLIENT_ID", "com.example.web")
    assert auth.provider_status()["apple"] is True and "appleid.auth.js" in c.get("/giris").text
    assert c.post("/giris/apple", json={"id_token": "x.y.z"}).status_code == 401
    assert c.post("/giris/apple", data="not json").status_code == 401
    ok = c.post("/giris/apple", json={"id_token": token})
    assert ok.status_code == 200 and "set-cookie" in ok.headers


def test_apple_keys_are_cached_and_a_failure_gives_the_stale_copy(monkeypatch):
    from src.web import auth

    calls = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"keys": [{"kid": "a"}]}).encode()

    def fake(url, timeout):
        calls.append(url)
        return R()

    monkeypatch.setattr(auth.urllib.request, "urlopen", fake)
    monkeypatch.setitem(auth._keys_cache, "keys", [])
    monkeypatch.setitem(auth._keys_cache, "at", 0.0)
    assert auth.apple_keys(1000.0) == [{"kid": "a"}] and auth.apple_keys(1500.0) == [{"kid": "a"}]
    assert len(calls) == 1  # cached for an hour
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda url, timeout: (_ for _ in ()).throw(auth.urllib.error.URLError("x")))  # fmt: skip
    assert auth.apple_keys(9999.0) == [{"kid": "a"}]  # unreachable: the stale copy, never an exception


def test_vercel_bundle_carries_the_results_store_and_the_cloud_job_redeploys_it(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("mvb", ROOT / "scripts" / "make_vercel_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = tmp_path / "p"
    (fake / "artifacts" / "results").mkdir(parents=True)
    (fake / "artifacts" / "results" / "EPL.json").write_text("{}", encoding="utf-8")
    for d in ("src", "configs"):
        (fake / d).mkdir()
        (fake / d / "x").write_text("", encoding="utf-8")
    (fake / "requirements-api.lock").write_text("a==1\n", encoding="utf-8")
    assert "artifacts/results/EPL.json" in mod.build(tmp_path / "out", fake)
    wf = (ROOT / ".github" / "workflows" / "markets-cloud.yml").read_text(encoding="utf-8")
    assert (
        "VERCEL_TOKEN" in wf and "vercel@latest deploy --prod" in wf and "exit 0" in wf
    )  # skipped without the token


def test_brand_contact_and_kvkk_sections_are_on_the_public_legal_pages(tmp_path):
    from src.web import brand

    assert brand.APP_NAME == "Welliddia" and brand.OWNER == "Veli Parlak" and brand.COUNTRY == "Türkiye"
    c = TestClient(create_app(tmp_path, keys=[]), follow_redirects=False)
    privacy, terms, login = c.get("/gizlilik").text, c.get("/kosullar").text, c.get("/giris").text
    for page in (privacy, terms):
        assert "veliparlak84@gmail.com" in page and "Veli Parlak" in page and "Welliddia" in page
    assert (
        "6698 sayılı" in privacy
        and "KVKK" in privacy
        and "Vercel (ABD)" in privacy
        and "Veri sorumlusu" in privacy
    )
    assert "Türkiye Cumhuriyeti hukukuna" in terms
    assert privacy.index("Veri sorumlusu") < privacy.index("Bu belge hakkında")  # the draft notice stays last
    assert "<h1>Welliddia</h1>" in login and "Maç Tahmin" not in login
    assert c.get("/manifest.webmanifest").json()["name"] == "Welliddia"
    en = translate_html(privacy)
    assert "Data controller" in en and "Law No. 6698" in en and "Contact and data deletion requests:" in en
