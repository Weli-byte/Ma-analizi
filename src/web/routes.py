"""HTTP routes of the public web UI (ADR 0051). Pages are public (no API key): they only show derived values."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.dashboard.viewmodel import intel_section

from . import pages


def register(app: FastAPI, service, root, now_fn) -> None:
    def upcoming() -> list[dict]:
        return intel_section(root, now_fn())

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(lig: str | None = None):
        return pages.home_page(upcoming(), lig if lig in pages.LEAGUES else None)

    @app.get("/mac/{fixture_id}", response_class=HTMLResponse, include_in_schema=False)
    def match(fixture_id: str):
        found = next((i for i in upcoming() if i["fixture_id"] == fixture_id), None)
        if found is None:
            return HTMLResponse(
                pages.not_found("Bu maç için tahmin bulunamadı (bitmiş ya da henüz yayınlanmamış olabilir)."),
                404,
            )
        picks = [p for p in service.value_picks()["picks"] if p["fixture_id"] == fixture_id]
        return pages.match_page(found, picks)

    @app.get("/tavsiyeler", response_class=HTMLResponse, include_in_schema=False)
    def tips():
        names = {i["fixture_id"]: (i["home"], i["away"]) for i in upcoming()}
        return pages.tips_page(service.tips(0.55), names)

    @app.get("/basari-kaydi", response_class=HTMLResponse, include_in_schema=False)
    def track():
        return pages.track_page(service.track_record())

    @app.get("/hakkinda", response_class=HTMLResponse, include_in_schema=False)
    def about():
        return pages.about_page()
