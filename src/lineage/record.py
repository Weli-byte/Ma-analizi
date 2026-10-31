"""The lineage block every new published forecast carries (ADR 0091).

`build()` is called by `src.markets.run.write_artifact` BEFORE the content hash is computed, so the lineage is covered by the hash. Fields:
  schema            "lineage-v1"
  code_commit       the git commit that produced the forecast (GITHUB_SHA in the cloud job, `git rev-parse HEAD` locally, "unknown" otherwise)
  pipeline          the producing entry point
  stage             where the forecast sits relative to kick-off when it was made: early (more than 24 h before), t-24h, t-90m, t-30m, post_kickoff
  minutes_to_kickoff
  history           the content-derived data version of the fitted history and the number of matches it held
  odds              which bookmaker quote set, observed when, of what timestamp quality (no prices: the raw feed is not redistributed)
  calibration       the calibration applied ("none": the identity is the champion, ADR 0089)
  lineups, injuries the status of those inputs (information only, never a feature)
Forecasts written before this block existed are NOT back-filled; the audit and the lineage query label them `legacy_unknown`.
"""

import functools
import os
import subprocess
from datetime import datetime
from pathlib import Path

SCHEMA = "lineage-v1"
REPO = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=1)
def code_commit() -> str:
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if sha:
        return sha
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, cwd=REPO)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def stage_of(kickoff: datetime, now: datetime) -> tuple[str, float]:
    minutes = (kickoff - now).total_seconds() / 60
    if minutes < 0:
        return "post_kickoff", minutes
    if minutes < 30:
        return "t-30m", minutes
    if minutes < 90:
        return "t-90m", minutes
    if minutes < 24 * 60:
        return "t-24h", minutes
    return "early", minutes


def build(now: datetime, kickoff: datetime, data_version: str, history_matches: int | None, odds: dict | None, pipeline: str = "src.markets.run") -> dict:
    stage, minutes = stage_of(kickoff, now)
    return {
        "schema": SCHEMA, "code_commit": code_commit(), "pipeline": pipeline, "stage": stage, "minutes_to_kickoff": round(minutes, 1),
        "history": {"data_version": data_version, "matches": history_matches},
        "odds": None if not odds else {"bookmaker": odds.get("bookmaker"), "observed_at": odds.get("observed_at"), "quality": odds.get("quality")},
        "calibration": "none (identity champion, ADR 0089)", "lineups": "information only, not a feature (ADR 0081)", "injuries": "information only, not a feature (ADR 0057)",
    }  # fmt: skip
