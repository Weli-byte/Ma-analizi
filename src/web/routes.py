"""HTTP routes of the mobile-style web app (ADR 0052). Every page except the login screen needs a session (guest or
signed-in); the data shown is derived values only. `/v1/*` keeps its own API-key rules."""

import json
import re
import secrets
import struct
import unicodedata
import zlib
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from src.dashboard.viewmodel import intel_section

from . import auth, pages, phone, plans, session
from .brand import APP_NAME
from .i18n import LANG_COOKIE, translate_html
from .logos import LogoIndex
from .store import UserStore


@lru_cache(maxsize=4)
def icon_png(size: int) -> bytes:
    """A plain green app icon with a white ball (no third-party artwork), generated with the standard library."""
    cx = cy = size / 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / size
            if d < 0.30:
                px = (255, 255, 255) if not (0.10 < d < 0.13) else (0, 122, 61)
            else:
                px = (0, 166, 81)
            rows += bytes(px)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


MANIFEST = {
    "name": APP_NAME, "short_name": APP_NAME, "start_url": "/", "display": "standalone",
    "background_color": "#00a651", "theme_color": "#00a651", "lang": "tr",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    ],
}  # fmt: skip
SW = "self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',()=>{});"


async def _form(request: Request) -> dict[str, str]:
    """application/x-www-form-urlencoded body as a dict (no python-multipart dependency)."""
    from urllib.parse import parse_qs

    raw = (await request.body())[:4096].decode("utf-8", "ignore")
    return {k: v[0] for k, v in parse_qs(raw).items()}


def image_type(f: Path) -> str:
    return "image/svg+xml" if f.suffix == ".svg" else "image/png"


# A third-party SVG opened directly on our origin could run script: never let it (sandboxed, no sniffing).
IMAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


def image_response(f: Path | None) -> Response:
    return (
        FileResponse(f, media_type=image_type(f), headers=IMAGE_HEADERS) if f else Response(status_code=404)
    )


_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def public_base(host: str | None) -> str | None:
    """`https://host` (http for localhost) when the Host header looks like a hostname; None for anything else, so a forged
    header can never put markup or another scheme into the sitemap or the link-preview tags."""
    if not host or len(host) > 255 or not _HOST.match(host):
        return None
    return f"{'http' if host.startswith(('localhost', '127.0.0.1')) else 'https'}://{host}"


def register(app: FastAPI, service, root, now_fn) -> None:
    logos = LogoIndex(root)
    store = UserStore(root)

    def upcoming() -> list[dict]:
        return intel_section(root, now_fn())

    def user_of(request: Request):
        return session.verify(request.cookies.get(session.COOKIE))

    def start(user: session.User, target: str = "/"):
        r = RedirectResponse(target, 303)
        r.set_cookie(
            session.COOKIE,
            session.sign(user),
            max_age=session.MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=False,
        )
        return r

    def plan_of(u) -> str:
        return plans.effective_plan(u.email, store.plan(u.uid))

    def guarded(request: Request):
        """(user, redirect): the redirect goes to the login screen when there is no valid session."""
        u = user_of(request)
        return (u, None) if u else (None, RedirectResponse("/giris", 303))

    # ------------------------------------------------------------------ app shell and assets (public)
    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return Response(SW, media_type="application/javascript")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(icon_png(192), media_type="image/png")

    @app.get("/icon-{size}.png", include_in_schema=False)
    def icon(size: int):
        if size not in (192, 512):
            return Response(status_code=404)
        return Response(icon_png(size), media_type="image/png")

    @app.get("/logo/team/{team_id}", include_in_schema=False)
    def team_logo(team_id: str):
        f = logos.team(team_id)
        return image_response(f)

    @app.get("/logo/league/{key}", include_in_schema=False)
    def league_logo(key: str):
        f = logos.league(key)
        return image_response(f)

    # ------------------------------------------------------------------------------------------ sign-in
    @app.get("/giris", response_class=HTMLResponse, include_in_schema=False)
    def login(request: Request):
        if user_of(request):
            return RedirectResponse("/", 303)
        return pages.login_page(auth.provider_status(), auth.google_client_id(), auth.apple_client_id())

    @app.post("/giris/misafir", include_in_schema=False)
    def guest():
        user = session.User("guest", "Misafir", None, f"guest:{secrets.token_hex(8)}")
        store.touch(user.uid, user.provider, user.name, user.email)
        return start(user)

    @app.post("/giris/google", include_in_schema=False)
    async def google(request: Request):
        try:
            token = (await request.json()).get("credential", "")
        except ValueError:
            token = ""
        user = auth.verify_google(token)
        if user is None:
            return JSONResponse({"error": "invalid google credential"}, 401)
        store.touch(user.uid, user.provider, user.name, user.email)
        r = JSONResponse({"ok": True})
        r.set_cookie(
            session.COOKIE, session.sign(user), max_age=session.MAX_AGE, httponly=True, samesite="lax"
        )
        return r

    @app.post("/giris/apple", include_in_schema=False)
    async def apple(request: Request):
        try:
            token = (await request.json()).get("id_token", "")
        except ValueError:
            token = ""
        user = auth.verify_apple(token)
        if user is None:
            return JSONResponse({"error": "invalid apple credential"}, 401)
        store.touch(user.uid, user.provider, user.name, user.email)
        r = JSONResponse({"ok": True})
        r.set_cookie(
            session.COOKIE, session.sign(user), max_age=session.MAX_AGE, httponly=True, samesite="lax"
        )
        return r

    @app.post("/cikis", include_in_schema=False)
    def logout():
        r = RedirectResponse("/giris", 303)
        r.delete_cookie(session.COOKIE)
        return r

    # ---------------------------------------------------------------------------------- phone sign-in
    sms_per_number, sms_per_client = phone.Throttle(3, 600), phone.Throttle(10, 3600)
    off = "Telefonla giriş henüz etkin değil (SMS sağlayıcısı ayarlanmadı). Misafir olarak devam edebilirsin."

    @app.get("/giris/telefon", response_class=HTMLResponse, include_in_schema=False)
    def phone_form():
        if not phone.configured():
            return HTMLResponse(pages.phone_page("number", error=off), 503)
        return pages.phone_page("number")

    @app.post("/giris/telefon/kod", response_class=HTMLResponse, include_in_schema=False)
    async def phone_send(request: Request):
        if not phone.configured():
            return HTMLResponse(pages.phone_page("number", error=off), 503)
        form = await _form(request)
        e164 = phone.normalize(str(form.get("phone", "")))
        client = request.client.host if request.client else "?"
        if e164 is None:
            return HTMLResponse(
                pages.phone_page("number", error="Geçerli bir telefon numarası yaz (ör. +90 5xx xxx xx xx)."),
                400,
            )
        if not (sms_per_number.allow(e164) and sms_per_client.allow(client)):
            return HTMLResponse(
                pages.phone_page("number", error="Çok fazla deneme. Biraz sonra tekrar dene."), 429
            )
        if not phone.send_code(e164):
            return HTMLResponse(
                pages.phone_page("number", error="Kod gönderilemedi. Numarayı kontrol edip tekrar dene."), 502
            )
        r = HTMLResponse(pages.phone_page("code", masked="····" + e164[-4:]))
        r.set_cookie(
            "mt_phone",
            session.sign(session.User("phone-pending", e164)),
            max_age=600,
            httponly=True,
            samesite="lax",
        )
        return r

    @app.post("/giris/telefon/dogrula", include_in_schema=False)
    async def phone_verify(request: Request):
        pending = session.verify(request.cookies.get("mt_phone"))
        form = await _form(request)
        if pending is None or pending.provider != "phone-pending":
            return RedirectResponse("/giris/telefon", 303)
        tail = "····" + pending.name[-4:]
        if not sms_per_number.allow("check:" + pending.name):
            return HTMLResponse(pages.phone_page("code", masked=tail, error="Çok fazla deneme."), 429)
        if not phone.check_code(pending.name, str(form.get("code", "")).strip()):
            return HTMLResponse(
                pages.phone_page("code", masked=tail, error="Kod yanlış ya da süresi dolmuş."), 400
            )
        user = phone.user_for(pending.name)
        store.touch(user.uid, user.provider, user.name, None)
        r = start(user)
        r.delete_cookie("mt_phone")
        return r

    # ------------------------------------------------------------------------------------------ language
    @app.middleware("http")
    async def secure_cookies(request: Request, call_next):
        """Behind HTTPS (directly or through a proxy that sets X-Forwarded-Proto) every cookie we set is marked Secure."""
        resp = await call_next(request)
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            resp.raw_headers = [
                (k, v + b"; Secure" if k.lower() == b"set-cookie" and b"secure" not in v.lower() else v)
                for k, v in resp.raw_headers
            ]
        return resp

    @app.middleware("http")
    async def english_ui(request: Request, call_next):
        resp = await call_next(request)
        if (
            request.cookies.get(LANG_COOKIE) == "en"
            and not request.url.path.startswith("/v1")
            and resp.headers.get("content-type", "").startswith("text/html")
        ):
            body = b"".join([chunk async for chunk in resp.body_iterator])
            html = translate_html(body.decode("utf-8")).encode("utf-8")
            out = Response(html, status_code=resp.status_code, media_type="text/html")
            out.raw_headers = [
                (k, v) for k, v in resp.raw_headers if k.lower() not in (b"content-length", b"content-type")
            ] + [
                (b"content-length", str(len(html)).encode()),
                (b"content-type", b"text/html; charset=utf-8"),
            ]
            return out
        return resp

    @app.get("/dil/{lang}", include_in_schema=False)
    def set_language(request: Request, lang: str):
        back = urlparse(request.headers.get("referer", "")).path or "/"
        r = RedirectResponse(
            back
            if back.startswith("/") and not back.startswith("//") and not back.startswith("/dil/")
            else "/",
            303,
        )
        r.set_cookie(LANG_COOKIE, "en" if lang == "en" else "tr", max_age=365 * 24 * 3600, samesite="lax")
        return r

    # ----------------------------------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        u, redirect = guarded(request)
        return redirect or pages.home_page(u.name, upcoming(), now_fn(), logos, store.favorites(u.uid))

    @app.get("/ligler", response_class=HTMLResponse, include_in_schema=False)
    def leagues(request: Request):
        _, redirect = guarded(request)
        return redirect or pages.leagues_page(upcoming(), now_fn(), logos)

    @app.get("/lig/{key}", response_class=HTMLResponse, include_in_schema=False)
    def league(request: Request, key: str, hafta: str = "bu"):
        _, redirect = guarded(request)
        if redirect:
            return redirect
        if key not in pages.LEAGUES:
            return HTMLResponse(pages.not_found("Böyle bir lig yok."), 404)
        return pages.league_page(
            key, upcoming(), hafta if hafta in ("bu", "gelecek", "hepsi") else "bu", now_fn(), logos
        )

    @app.get("/mac/{fixture_id}", response_class=HTMLResponse, include_in_schema=False)
    def match(request: Request, fixture_id: str):
        u, redirect = guarded(request)
        if redirect:
            return redirect
        found = next((i for i in upcoming() if i["fixture_id"] == fixture_id), None)
        if found is None:
            return HTMLResponse(
                pages.not_found("Bu maç için tahmin bulunamadı (bitmiş ya da henüz yayınlanmamış olabilir)."),
                404,
            )
        picks = [p for p in service.value_picks()["picks"] if p["fixture_id"] == fixture_id]
        from src.markets.injuries import latest_for

        injuries = latest_for(
            root, found["home_team_id"], found["away_team_id"], datetime.fromisoformat(found["kickoff_utc"])
        )
        from src.dashboard.viewmodel import _names
        from src.markets.people import people_for

        people = people_for(
            root, found["home_team_id"], found["away_team_id"], datetime.fromisoformat(found["kickoff_utc"])
        )
        return pages.match_page(
            found,
            picks,
            logos,
            store.favorites(u.uid),
            injuries,
            people,
            _names(root),
            locked=plans.locked_tabs(plan_of(u)),
            exp_status=service.track_record().get("experimental"),
        )

    @app.get("/tavsiyeler", response_class=HTMLResponse, include_in_schema=False)
    def tips(request: Request):
        u, redirect = guarded(request)
        if redirect:
            return redirect
        names = {i["fixture_id"]: (i["home"], i["away"]) for i in upcoming()}
        return pages.tips_page(plans.filter_tips(service.tips(0.55), plan_of(u)), names)

    @app.get("/profil", response_class=HTMLResponse, include_in_schema=False)
    def profile(request: Request):
        u, redirect = guarded(request)
        return redirect or pages.profile_page(u, plan_of(u))

    @app.get("/basari-kaydi", response_class=HTMLResponse, include_in_schema=False)
    def track(request: Request):
        _, redirect = guarded(request)
        return redirect or pages.track_page(service.track_record())

    def site_base(request: Request) -> str | None:
        return public_base(request.headers.get("host") or request.url.netloc)

    @app.middleware("http")
    async def remember_request_url(request: Request, call_next):
        """Lets pages.meta_tags build absolute link-preview URLs without threading the request through every page."""
        base = site_base(request)
        token = pages.REQUEST_URL.set((base, request.url.path) if base else None)
        try:
            return await call_next(request)
        finally:
            pages.REQUEST_URL.reset(token)

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots(request: Request):
        """Public pages may be indexed; account, API and signed-in routes may not."""
        disallow = ("/v1/", "/giris/", "/profil", "/favori", "/favoriler", "/hesabi-sil", "/cikis")
        base = site_base(request)
        lines = ["User-agent: *", *[f"Disallow: {p}" for p in disallow]]
        if base:
            lines.append(f"Sitemap: {base}/sitemap.xml")
        return "\n".join(lines) + "\n"

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request):
        base = site_base(request)
        if not base:
            return Response(status_code=400)
        urls = "".join(
            f"<url><loc>{base}{p}</loc></url>" for p in ("/giris", "/gizlilik", "/kosullar", "/sunum")
        )
        xml = f"<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{urls}</urlset>"
        return Response(xml, media_type="application/xml")

    @app.get("/gizlilik", response_class=HTMLResponse, include_in_schema=False)
    def privacy():  # public on purpose: an OAuth consent screen needs a reachable policy URL
        return pages.legal_page("privacy")

    @app.get("/kosullar", response_class=HTMLResponse, include_in_schema=False)
    def terms():
        return pages.legal_page("terms")

    @app.get("/hakkinda", response_class=HTMLResponse, include_in_schema=False)
    def about(request: Request):
        _, redirect = guarded(request)
        return redirect or pages.about_page()

    # --------------------------------------------------------------- favourites, search, account, summary
    def team_names() -> dict[str, str]:
        out: dict[str, str] = {}
        for i in upcoming():
            out[i["home_team_id"]], out[i["away_team_id"]] = i["home"], i["away"]
        return out

    @app.get("/favoriler", response_class=HTMLResponse, include_in_schema=False)
    def favorites(request: Request):
        u, redirect = guarded(request)
        if redirect:
            return redirect
        favs = store.favorites(u.uid)
        names = team_names()
        mine = [i for i in upcoming() if i["home_team_id"] in favs or i["away_team_id"] in favs]
        return pages.favorites_page(
            mine, [(t, names.get(t, t.split("_", 1)[-1].replace("_", " ").title())) for t in favs], logos
        )

    @app.post("/favori/{team_id}", include_in_schema=False)
    def toggle_favorite(request: Request, team_id: str, next: str = "/favoriler"):
        u, redirect = guarded(request)
        if redirect:
            return redirect
        if team_id.count("_") >= 1 and len(team_id) < 80:
            store.toggle(u.uid, team_id)
        return RedirectResponse(
            next if next.startswith("/") and not next.startswith("//") else "/favoriler", 303
        )

    @app.get("/ara", response_class=HTMLResponse, include_in_schema=False)
    def search(request: Request, q: str = ""):
        _, redirect = guarded(request)
        if redirect:
            return redirect
        key = _norm(q)
        hits = (
            [i for i in upcoming() if key and (key in _norm(i["home"]) or key in _norm(i["away"]))]
            if len(q) <= 60
            else []
        )
        return pages.search_page(q[:60], hits, logos)

    @app.post("/hesabi-sil", include_in_schema=False)
    def delete_account(request: Request):
        u, redirect = guarded(request)
        if redirect:
            return redirect
        store.delete_user(u.uid)
        r = RedirectResponse("/giris", 303)
        r.delete_cookie(session.COOKIE)
        return r

    @app.get("/sunum", response_class=HTMLResponse, include_in_schema=False)
    def summary():
        return pages.sunum_page()  # public: easy to show to someone without signing in


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i")).encode("ascii", "ignore").decode()
    return "".join(c for c in t if c.isalnum() or c == " ").strip()
