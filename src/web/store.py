"""Small user store (ADR 0053): SQLite file `artifacts/users.sqlite` (never committed, never in the public repo).

Holds only what the app needs: a user id, provider, display name, optional e-mail (Google), the time first/last seen,
and the favourite clubs. No password, no phone number, no token. A guest id is a random value kept in the session
cookie. NOTE: on a host with an ephemeral disk the file is lost on restart (favourites reset)."""

import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, provider TEXT NOT NULL, name TEXT NOT NULL, email TEXT,
                                 created_at TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS favorites(uid TEXT NOT NULL, team_id TEXT NOT NULL, added_at TEXT NOT NULL,
                                     PRIMARY KEY(uid, team_id));
"""


class UserStore:
    def __init__(self, root: Path):
        # FORECAST_USER_DB: a path on a persistent disk (a host's volume); default = inside the project
        override = os.environ.get("FORECAST_USER_DB")
        self.path = Path(override) if override else Path(root) / "artifacts" / "users.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        """A committed-and-CLOSED connection per call (no leaked file handles)."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            with db:  # commit or roll back
                yield db

    def touch(self, uid: str, provider: str, name: str, email: str | None) -> None:
        now = datetime.now(UTC).isoformat()
        with self._db() as db:
            db.execute(
                "INSERT INTO users(uid, provider, name, email, created_at, last_seen) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET name=excluded.name, email=excluded.email, last_seen=excluded.last_seen",
                (uid, provider, name, email, now, now),
            )

    def favorites(self, uid: str) -> list[str]:
        with self._db() as db:
            return [
                r[0]
                for r in db.execute("SELECT team_id FROM favorites WHERE uid=? ORDER BY added_at", (uid,))
            ]

    def toggle(self, uid: str, team_id: str) -> bool:
        """True when the club is a favourite after the call."""
        with self._db() as db:
            if db.execute("SELECT 1 FROM favorites WHERE uid=? AND team_id=?", (uid, team_id)).fetchone():
                db.execute("DELETE FROM favorites WHERE uid=? AND team_id=?", (uid, team_id))
                return False
            db.execute(
                "INSERT INTO favorites(uid, team_id, added_at) VALUES(?,?,?)",
                (uid, team_id, datetime.now(UTC).isoformat()),
            )
            return True

    def delete_user(self, uid: str) -> None:
        """Remove everything stored about a user (the 'delete my data' action)."""
        with self._db() as db:
            db.execute("DELETE FROM favorites WHERE uid=?", (uid,))
            db.execute("DELETE FROM users WHERE uid=?", (uid,))
