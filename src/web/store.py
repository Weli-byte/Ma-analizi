"""Small user store (ADR 0053, ADR 0061): favourites and the minimal account record.

Holds only what the app needs: a user id, provider, display name, optional e-mail (Google), the time first/last seen,
and the favourite clubs. No password, no phone number, no token. A guest id is a random value kept in the session
cookie.

Two backends with the same SQL:
- SQLite file `artifacts/users.sqlite` (or `FORECAST_USER_DB`); never committed. On a host with an ephemeral disk the file is
  lost on restart (favourites reset).
- Turso (libSQL, hosted SQLite) over its HTTPS API, selected when `FORECAST_TURSO_URL` is set; needs `FORECAST_TURSO_TOKEN`.
  Persistent across restarts. TLS is always verified; a plain-http URL is refused. A half-configured Turso (URL without
  token) fails loudly at start, it never falls back to the SQLite file."""

import json
import os
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL, email TEXT, "
    "created_at TEXT NOT NULL, last_seen TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free')",
    "CREATE TABLE IF NOT EXISTS favorites(uid TEXT NOT NULL, team_id TEXT NOT NULL, added_at TEXT NOT NULL, "
    "PRIMARY KEY(uid, team_id))",
)
ADD_PLAN_COLUMN = "ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'"
TIMEOUT_SECONDS = 10


class StoreError(RuntimeError):
    """The user store could not run a statement (misconfiguration, network, or a database error)."""


class Result(NamedTuple):
    rows: list[tuple]
    affected: int


Statement = tuple[str, tuple]


def _arg(value: Any) -> dict:
    """One bound parameter in the Hrana/Turso JSON encoding (numbers travel as strings to keep precision)."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"unsupported SQL parameter type: {type(value).__name__}")
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": value}


def _cell(cell: dict) -> Any:
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "text":
        return cell["value"]
    raise StoreError(f"unsupported Turso value type: {kind!r}")


def pipeline_body(statements: list[Statement]) -> dict:
    """Request body of Turso's `POST /v2/pipeline`: every statement, then `close`."""
    requests: list[dict] = [
        {"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in args]}} for sql, args in statements
    ]
    requests.append({"type": "close"})
    return {"requests": requests}


def parse_pipeline(payload: dict, count: int) -> list[Result]:
    """Results of the `count` execute requests; any error entry raises `StoreError` with the server's message."""
    results = payload.get("results", [])
    if len(results) < count:
        raise StoreError(f"Turso returned {len(results)} results for {count} statements")
    out: list[Result] = []
    for entry in results[:count]:
        if entry.get("type") != "ok":
            message = (entry.get("error") or {}).get("message", "unknown error")
            raise StoreError(f"Turso statement failed: {message}")
        result = entry["response"]["result"]
        out.append(
            Result(
                [tuple(_cell(c) for c in row) for row in result.get("rows", [])],
                result.get("affected_row_count", 0),
            )
        )
    return out


def turso_endpoint(url: str) -> str:
    """`libsql://db-org.turso.io` (as the Turso console shows it) -> `https://db-org.turso.io`. Plain http is refused."""
    url = url.strip().rstrip("/")
    for scheme in ("libsql://", "turso://"):
        if url.startswith(scheme):
            url = "https://" + url[len(scheme) :]
    if not url.startswith("https://"):
        raise StoreError("FORECAST_TURSO_URL must be a libsql:// or https:// address (TLS is required)")
    return url


class _SqliteBackend:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def run(self, statements: list[Statement]) -> list[Result]:
        """All statements in ONE committed-and-CLOSED connection (no leaked file handles)."""
        out: list[Result] = []
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            with db:  # commit or roll back
                for sql, args in statements:
                    cur = db.execute(sql, args)
                    out.append(Result([tuple(r) for r in cur.fetchall()], max(cur.rowcount, 0)))
        return out


class _TursoBackend:
    def __init__(self, url: str, token: str):
        self.endpoint = turso_endpoint(url) + "/v2/pipeline"
        self.token = token

    def run(self, statements: list[Statement]) -> list[Result]:
        body = json.dumps(pipeline_body(statements)).encode()
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT_SECONDS
            ) as response:  # default context: TLS verified
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise StoreError(f"Turso answered HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise StoreError(f"Turso unreachable: {exc}") from exc
        return parse_pipeline(payload, len(statements))


def _backend_from_env(root: Path):
    url = os.environ.get("FORECAST_TURSO_URL")
    if url:
        token = os.environ.get("FORECAST_TURSO_TOKEN")
        if not token:
            raise StoreError("FORECAST_TURSO_URL is set but FORECAST_TURSO_TOKEN is missing")
        return _TursoBackend(url, token)
    # FORECAST_USER_DB: a path on a persistent disk (a host's volume); default = inside the project
    override = os.environ.get("FORECAST_USER_DB")
    return _SqliteBackend(Path(override) if override else Path(root) / "artifacts" / "users.sqlite")


class UserStore:
    def __init__(self, root: Path):
        self._backend = _backend_from_env(root)
        self._ready = False
        if isinstance(self._backend, _SqliteBackend):
            self._ensure()  # local file: create the tables now; the remote database is prepared on first use

    @property
    def backend(self) -> str:
        return "turso" if isinstance(self._backend, _TursoBackend) else "sqlite"

    @property
    def path(self) -> Path | None:
        return self._backend.path if isinstance(self._backend, _SqliteBackend) else None

    def _ensure(self) -> None:
        if not self._ready:
            self._backend.run([(sql, ()) for sql in SCHEMA_STATEMENTS])
            (info,) = self._backend.run([("PRAGMA table_info(users)", ())])
            if "plan" not in {row[1] for row in info.rows}:  # a table created before ADR 0062
                self._backend.run([(ADD_PLAN_COLUMN, ())])
            self._ready = True

    def _run(self, statements: list[Statement]) -> list[Result]:
        self._ensure()
        return self._backend.run(statements)

    def touch(self, uid: str, provider: str, name: str, email: str | None) -> None:
        now = datetime.now(UTC).isoformat()
        self._run(
            [
                (
                    "INSERT INTO users(uid, provider, name, email, created_at, last_seen) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(uid) DO UPDATE SET name=excluded.name, email=excluded.email, last_seen=excluded.last_seen",
                    (uid, provider, name, email, now, now),
                )
            ]
        )

    def plan(self, uid: str) -> str:
        """The stored plan ('free' for an unknown user); the effective plan also looks at the e-mail list (`plans.py`)."""
        (res,) = self._run([("SELECT plan FROM users WHERE uid=?", (uid,))])
        return res.rows[0][0] if res.rows else "free"

    def set_plan(self, uid: str, plan: str) -> None:
        if plan not in ("free", "pro"):
            raise ValueError(f"unknown plan: {plan!r}")
        self._run([("UPDATE users SET plan=? WHERE uid=?", (plan, uid))])

    def favorites(self, uid: str) -> list[str]:
        (res,) = self._run([("SELECT team_id FROM favorites WHERE uid=? ORDER BY added_at", (uid,))])
        return [r[0] for r in res.rows]

    def toggle(self, uid: str, team_id: str) -> bool:
        """True when the club is a favourite after the call."""
        (deleted,) = self._run([("DELETE FROM favorites WHERE uid=? AND team_id=?", (uid, team_id))])
        if deleted.affected:
            return False
        self._run(
            [
                (
                    "INSERT OR IGNORE INTO favorites(uid, team_id, added_at) VALUES(?,?,?)",
                    (uid, team_id, datetime.now(UTC).isoformat()),
                )
            ]
        )
        return True

    def delete_user(self, uid: str) -> None:
        """Remove everything stored about a user (the 'delete my data' action)."""
        self._run(
            [
                ("DELETE FROM favorites WHERE uid=?", (uid,)),
                ("DELETE FROM users WHERE uid=?", (uid,)),
            ]
        )
