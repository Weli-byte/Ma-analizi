"""Versioned public API (S18, ADR 0035). `uvicorn src.api.app:create_app --factory`.

- Auth: API key in `X-API-Key`; accepted keys come from `FORECAST_API_KEYS` (comma separated, never in
  code). Fail closed: with no key configured every /v1 data route answers 503 `auth_not_configured`.
- Rate limit: fixed window per key (`FORECAST_API_RATE_PER_MIN`, default 60), `429` + `Retry-After`.
- Pagination: `limit` (1..100) and `offset`. One error schema: {"error": {code, message, request_id}}.
- Data comes from `ForecastService` only (no storage structure is exposed). Research data
  (RESEARCH_ONLY licences): `meta.license_status` says so on every response.
"""

import hashlib
import hmac
import os
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .service import ForecastService

API_VERSION = "v1"
LICENSE_STATUS = "RESEARCH_ONLY"


class RateLimiter:
    """Fixed-window limiter keyed by API-key digest (never the key itself)."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self.windows: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))

    def check(self, key_id: str) -> int | None:
        """None if allowed, else seconds until the window resets."""
        now = self.clock()
        window = int(now // 60)
        w, n = self.windows[key_id]
        if w != window:
            w, n = window, 0
        if n >= self.per_minute:
            return max(1, int(60 - (now % 60)))
        self.windows[key_id] = (w, n + 1)
        return None


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _err(status: int, code: str, message: str, request: Request, headers: dict | None = None):
    rid = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": rid}}, status, headers=headers
    )


def create_app(
    root: Path | str = ".",
    keys: list[str] | None = None,
    per_minute: int | None = None,
    now_fn=None,
    clock=None,
) -> FastAPI:
    now_fn = now_fn or (lambda: datetime.now(UTC))
    if keys is None:
        keys = [k.strip() for k in os.environ.get("FORECAST_API_KEYS", "").split(",") if k.strip()]
    digests = {_digest(k) for k in keys}
    limiter = RateLimiter(
        per_minute or int(os.environ.get("FORECAST_API_RATE_PER_MIN", "60")), clock or time.monotonic
    )
    service = ForecastService(Path(root), now_fn)
    app = FastAPI(
        title="Football Forecasting API",
        version="1.0.0",
        description="Probabilistic 1X2 forecasts with full provenance. Research data, paper-only "
        "value analytics, not betting advice.",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _err(exc.status_code, code, str(exc.detail), request, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _err(422, "invalid_request", fields, request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return _err(500, "internal_error", "internal error", request)  # detail goes to logs, not clients

    def auth(request: Request) -> str:
        if not digests:
            raise HTTPException(503, "no API key is configured on this server (set FORECAST_API_KEYS)")
        presented = request.headers.get("X-API-Key", "")
        d = _digest(presented)
        if not presented or not any(hmac.compare_digest(d, ok) for ok in digests):
            raise HTTPException(401, "missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"})
        wait = limiter.check(d[:12])
        if wait is not None:
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(wait)})
        return d[:12]

    def envelope(data, request: Request, page: dict | None = None) -> dict:
        meta = {
            "api_version": API_VERSION, "generated_at": now_fn().isoformat(),
            "request_id": request.state.request_id, "license_status": LICENSE_STATUS,
        }  # fmt: skip
        out = {"data": data, "meta": meta}
        if page:
            out["page"] = page
        return out

    def paged(items: list, limit: int, offset: int, request: Request) -> dict:
        return envelope(
            items[offset : offset + limit], request, {"limit": limit, "offset": offset, "total": len(items)}
        )

    Limit = Query(20, ge=1, le=100)
    Offset = Query(0, ge=0)

    @app.get("/v1/health", tags=["system"])
    def health(request: Request):
        return envelope({"status": "ok", "auth_configured": bool(digests)}, request)

    @app.get("/v1/fixtures", tags=["fixtures"], dependencies=[Depends(auth)])
    def fixtures(request: Request, upcoming_only: bool = True, league: str | None = None,
                 limit: int = Limit, offset: int = Offset):  # fmt: skip
        return paged(service.fixtures(upcoming_only, league), limit, offset, request)

    @app.get("/v1/fixtures/{fixture_id}", tags=["fixtures"], dependencies=[Depends(auth)])
    def fixture(fixture_id: str, request: Request):
        f = service.fixture(fixture_id)
        if f is None:
            raise HTTPException(404, f"unknown fixture {fixture_id!r}")
        return envelope(f, request)

    @app.get("/v1/fixtures/{fixture_id}/predictions", tags=["fixtures"], dependencies=[Depends(auth)])
    def predictions(fixture_id: str, request: Request, limit: int = Limit, offset: int = Offset):
        p = service.predictions(fixture_id)
        if p is None:
            raise HTTPException(404, f"unknown fixture {fixture_id!r}")
        return paged(p, limit, offset, request)

    @app.get("/v1/fixtures/{fixture_id}/intelligence", tags=["fixtures"], dependencies=[Depends(auth)])
    def intelligence(fixture_id: str, request: Request):
        i = service.intelligence(fixture_id)
        if i is None:
            raise HTTPException(
                404, f"no match intelligence for {fixture_id!r} (run python -m src.markets.run)"
            )
        return envelope(i, request)

    @app.get("/v1/tips", tags=["research"], dependencies=[Depends(auth)])
    def tips(request: Request, min_probability: float = Query(0.55, ge=0.5, le=1),
             upcoming_only: bool = True, limit: int = Limit, offset: int = Offset):  # fmt: skip
        return paged(service.tips(min_probability, upcoming_only), limit, offset, request)

    @app.get("/v1/models", tags=["models"], dependencies=[Depends(auth)])
    def models(request: Request, limit: int = Limit, offset: int = Offset):
        return paged(service.models(), limit, offset, request)

    @app.get("/v1/benchmarks", tags=["models"], dependencies=[Depends(auth)])
    def benchmarks(request: Request):
        return envelope(service.benchmarks(), request)

    @app.get("/v1/teams/{team_id}/forecast", tags=["teams"], dependencies=[Depends(auth)])
    def team_forecast(team_id: str, request: Request):
        t = service.team_forecast(team_id)
        if t is None:
            raise HTTPException(404, f"no fixture known for team {team_id!r}")
        return envelope(t, request)

    @app.get("/v1/value-research", tags=["research"], dependencies=[Depends(auth)])
    def value_research(request: Request):
        return envelope(service.value_research(), request)

    @app.get("/v1/value-picks", tags=["research"], dependencies=[Depends(auth)])
    def value_picks(request: Request, min_edge: float | None = Query(None, ge=0, le=1),
                    min_ev: float | None = Query(None, ge=0, le=5)):  # fmt: skip
        return envelope(service.value_picks(min_edge, min_ev), request)

    return app
