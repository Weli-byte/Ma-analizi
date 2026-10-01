"""CLI helpers: UTF-8-safe output regardless of the Windows console code page, and a minimal
`.env` loader (no new dependency -- `python-dotenv` would be one more package for ~10 lines of
logic)."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Sets `os.environ[KEY] = VALUE` for each `KEY=VALUE` line in `.env` (gitignored) -- never
    overrides a variable already set in the real environment, never raises on a missing or
    malformed file, never prints anything (the whole point is to keep secrets out of logs)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def configure_output() -> None:
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
