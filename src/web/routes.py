"""HTTP routes of the mobile-style web app (ADR 0052). Every page except the login screen needs a session (guest or
signed-in); the data shown is derived values only. `/v1/*` keeps its own API-key rules."""

import json
import struct
import zlib
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from src.dashboard.viewmodel import intel_section

from . import auth, pages, session
from .logos import LogoIndex


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
    "name": "Maç Tahmin", "short_name": "Maç Tahmin", "start_url": "/", "display": "standalone",
    "background_color": "#00a651", "theme_color": "#00a651", "lang": "tr",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    ],
}  # fmt: skip
SW = "self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',()=>{});"


def register(app: FastAPI, service, root, now_fn) -> None:
    logos = LogoIndex(root)

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

    @app.get("/icon-{size}.png", include_in_schema=False)
    def icon(size: int):
        if size not in (192, 512):
            return Response(status_code=404)
        return Response(icon_png(size), media_type="image/png")

    @app.get("/logo/team/{team_id}", include_in_schema=False)
    def team_logo(team_id: str):
        f = logos.team(team_id)
        return FileResponse(f, media_type="image/png") if f else Response(status_code=404)

    @app.get("/logo/league/{key}", include_in_schema=False)
    def league_logo(key: str):
        f = logos.league(key)
        return FileResponse(f, media_type="image/png") if f else Response(status_code=404)

    # ------------------------------------------------------------------------------------------ sign-in
    @app.get("/giris", response_class=HTMLResponse, include_in_schema=False)
    def login(request: Request):
        if user_of(request):
            return RedirectResponse("/", 303)
        return pages.login_page(auth.provider_status(), auth.google_client_id())

    @app.post("/giris/misafir", include_in_schema=False)
    def guest():
        return start(session.User("guest", "Misafir"))

    @app.post("/giris/google", include_in_schema=False)
    async def google(request: Request):
        try:
            token = (await request.json()).get("credential", "")
        except ValueError:
            token = ""
        user = auth.verify_google(token)
        if user is None:
            return JSONResponse({"error": "invalid google credential"}, 401)
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

    # ----------------------------------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request):
        u, redirect = guarded(request)
        return redirect or pages.home_page(u.name, upcoming(), now_fn(), logos)

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
        _, redirect = guarded(request)
        if redirect:
            return redirect
        found = next((i for i in upcoming() if i["fixture_id"] == fixture_id), None)
        if found is None:
            return HTMLResponse(
                pages.not_found("Bu maç için tahmin bulunamadı (bitmiş ya da henüz yayınlanmamış olabilir)."),
                404,
            )
        picks = [p for p in service.value_picks()["picks"] if p["fixture_id"] == fixture_id]
        return pages.match_page(found, picks, logos)

    @app.get("/tavsiyeler", response_class=HTMLResponse, include_in_schema=False)
    def tips(request: Request):
        _, redirect = guarded(request)
        names = {i["fixture_id"]: (i["home"], i["away"]) for i in upcoming()}
        return redirect or pages.tips_page(service.tips(0.55), names)

    @app.get("/profil", response_class=HTMLResponse, include_in_schema=False)
    def profile(request: Request):
        u, redirect = guarded(request)
        return redirect or pages.profile_page(u)

    @app.get("/basari-kaydi", response_class=HTMLResponse, include_in_schema=False)
    def track(request: Request):
        _, redirect = guarded(request)
        return redirect or pages.track_page(service.track_record())

    @app.get("/hakkinda", response_class=HTMLResponse, include_in_schema=False)
    def about(request: Request):
        _, redirect = guarded(request)
        return redirect or pages.about_page()
