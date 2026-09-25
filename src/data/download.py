"""Verified-TLS downloader with retries, explicit fallback and provenance sidecars.

    python -m src.data.download [--root DIR] [--seasons 2025-26 ...]

TLS verification is never disabled. If the primary source fails, an explicitly configured
fallback (configs/sources.yaml) may be used; the sidecar then records origin=archive.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output
from src.config import (
    DataConfig,
    LeagueFormat,
    LeaguesConfig,
    SourceEntry,
    SourcesConfig,
    config_dir_for,
    load_config,
)

from .leagues import season_to_code
from .raw_validation import RawFileError, sha256_bytes, validate_raw_bytes

Fetcher = Callable[[str, float], bytes]


class FetchError(RuntimeError):
    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


class DownloadError(RuntimeError):
    """All configured sources failed. The message lists every attempt and what to do."""


def default_fetch(url: str, timeout: float) -> bytes:
    ctx = ssl.create_default_context()  # verification ON, always
    req = urllib.request.Request(url, headers={"User-Agent": "football-forecasting-research/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            raise FetchError(f"TLS verification failed for {url}: {e.reason}", retryable=False) from e
        raise FetchError(f"network error for {url}: {e.reason}") from e
    except OSError as e:
        raise FetchError(f"network error for {url}: {e}") from e


def render_url(src: SourceEntry, league: LeagueFormat, season: str) -> str:
    return src.url_template.format(
        season_code=season_to_code(season), div=league.source_code, snapshot=src.snapshot or ""
    )


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_sidecar(provenance_dir: Path, record: dict) -> None:
    provenance_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write(provenance_dir / f"{record['filename']}.json", payload)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="seconds")


def download_file(
    league_id: str,
    league: LeagueFormat,
    season: str,
    sources: SourcesConfig,
    raw_dir: Path,
    provenance_dir: Path,
    fetch: Fetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    """Download one league-season. An existing file is replaced only by a fully valid new copy."""
    fetch = fetch or default_fetch  # resolved at call time (tests/CLIs may replace it)
    candidates = [sources.primary] + (sources.fallbacks if sources.allow_fallback else [])
    attempts: list[str] = []
    filename = f"{league.source_code}_{season_to_code(season)}.csv"
    for src in candidates:
        url = render_url(src, league, season)
        for attempt in range(1, sources.retries + 1):
            try:
                data = fetch(url, sources.timeout_seconds)
                validate_raw_bytes(filename, data, league)
            except FetchError as e:
                attempts.append(f"[{src.id}] attempt {attempt}: {e}")
                if not e.retryable:
                    break
                if attempt < sources.retries:
                    sleep(sources.backoff_seconds * 2 ** (attempt - 1))
                continue
            except RawFileError as e:
                attempts.append(f"[{src.id}] attempt {attempt}: invalid content: {e}")
                break  # the same URL will return the same bad content
            record = {
                "filename": filename,
                "source_id": src.id,
                "source_url": url,
                "origin": src.origin,
                "retrieved_at_utc": _iso(now()),
                "sha256": sha256_bytes(data),
                "note": "" if src.origin == "official" else f"fallback {src.id} ({src.snapshot})",
            }
            raw_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(raw_dir / filename, data)
            _write_sidecar(provenance_dir, record)
            return record
    manual_url = render_url(sources.primary, league, season)
    raise DownloadError(
        f"could not download {filename} ({league_id} {season}).\n  "
        + "\n  ".join(attempts)
        + f"\nAction: download {manual_url} in a browser, save it as {raw_dir / filename}, then run "
        f"`python -m src.data.download --register-manual {filename}`."
    )


def register_manual(
    raw_dir: Path,
    provenance_dir: Path,
    filename: str,
    source_url: str,
    league: LeagueFormat,
    when: datetime | None = None,
) -> dict:
    """Record provenance for a file a human downloaded (origin=manual). Validates content first."""
    data = (raw_dir / filename).read_bytes()
    validate_raw_bytes(filename, data, league)
    record = {
        "filename": filename,
        "source_id": "manual",
        "source_url": source_url,
        "origin": "manual",
        "retrieved_at_utc": _iso(when or datetime.now(UTC)),
        "sha256": sha256_bytes(data),
        "note": "downloaded by a human; retrieved_at is the registration time",
    }
    _write_sidecar(provenance_dir, record)
    return record


def download_all(
    root: Path,
    seasons: list[str] | None = None,
    fetch: Fetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict], list[str]]:
    cdir = config_dir_for(root)
    data_cfg: DataConfig = load_config("data", cdir)
    leagues: LeaguesConfig = load_config("leagues", cdir)
    sources: SourcesConfig = load_config("sources", cdir)
    done: list[dict] = []
    failed: list[str] = []
    for season in seasons or data_cfg.seasons:
        for lid in data_cfg.leagues:
            try:
                done.append(
                    download_file(
                        lid,
                        leagues.leagues[lid],
                        season,
                        sources,
                        root / data_cfg.raw_dir,
                        root / data_cfg.provenance_dir,
                        fetch=fetch,
                        sleep=sleep,
                    )
                )
            except DownloadError as e:
                failed.append(str(e))
    return done, failed


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    p.add_argument("--seasons", nargs="*", default=None)
    p.add_argument("--register-manual", metavar="FILENAME", default=None)
    p.add_argument("--source-url", default=None, help="URL the manual file was downloaded from")
    a = p.parse_args(argv)
    root = Path(a.root)
    if a.register_manual:
        from .manifest import parse_filename

        cdir = config_dir_for(root)
        data_cfg = load_config("data", cdir)
        leagues = load_config("leagues", cdir)
        lid, season = parse_filename(a.register_manual, leagues)
        sources = load_config("sources", cdir)
        url = a.source_url or render_url(sources.primary, leagues.leagues[lid], season)
        rec = register_manual(
            root / data_cfg.raw_dir,
            root / data_cfg.provenance_dir,
            a.register_manual,
            url,
            leagues.leagues[lid],
        )
        print(f"registered {rec['filename']} origin=manual sha256={rec['sha256'][:12]}")
        return 0
    done, failed = download_all(root, a.seasons)
    for d in done:
        print(f"ok {d['filename']} origin={d['origin']} sha256={d['sha256'][:12]}")
    for f in failed:
        print("FAILED:", f, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
