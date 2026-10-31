"""Read side of the stage records (ADR 0093): the current schedule's record of each stage for one fixture, in stage order."""

import json
from pathlib import Path

from .freeze import STAGES


def stages_for(root, fixture: str) -> list[dict]:
    d = Path(root) / "artifacts" / "stage_records" / fixture
    best: dict[str, dict] = {}
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        r = json.loads(f.read_text(encoding="utf-8"))
        if r["stage"] not in best or r["kickoff_utc"] > best[r["stage"]]["kickoff_utc"]:
            best[r["stage"]] = r
    return [best[s] for s, _ in STAGES if s in best]
