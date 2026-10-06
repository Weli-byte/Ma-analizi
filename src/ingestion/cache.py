"""S12: on-disk cache for raw provider responses -- doubles as the "raw response audit storage"
the sprint asks for (every response is kept, content-hashed, with when it was fetched), not just
a performance cache.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheEntry:
    key: str
    content_hash: str
    fetched_at_unix: float
    payload: dict


class ResponseCache:
    """One JSON file per entry, under `root/<key_hash>.json`. `ttl_seconds=None` means "never
    stale" (still cached, still audited, just never used to skip a fresh fetch)."""

    def __init__(self, root: Path, ttl_seconds: float | None = None, time_fn=time.time):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self._time = time_fn

    def _path(self, key: str) -> Path:
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.root / f"{key_hash}.json"

    def get(self, key: str) -> CacheEntry | None:
        path = self._path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["key"] != key:  # hash collision guard: never silently return the wrong entry
            return None
        entry = CacheEntry(data["key"], data["content_hash"], data["fetched_at_unix"], data["payload"])
        if self.ttl_seconds is not None and (self._time() - entry.fetched_at_unix) > self.ttl_seconds:
            return None  # stale for CACHING purposes; the file itself is left on disk (audit trail)
        return entry

    def put(self, key: str, payload: dict) -> CacheEntry:
        content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        entry = CacheEntry(key, content_hash, self._time(), payload)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(
            json.dumps(
                {"key": entry.key, "content_hash": entry.content_hash,
                 "fetched_at_unix": entry.fetched_at_unix, "payload": entry.payload},
                indent=2, sort_keys=True,
            ),  # fmt: skip
            encoding="utf-8",
        )
        return entry

    def get_or_fetch(self, key: str, fetch_fn) -> CacheEntry:
        cached = self.get(key)
        if cached is not None:
            return cached
        return self.put(key, fetch_fn())
