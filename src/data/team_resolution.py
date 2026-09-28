"""Team identity CLI (ADR 0010).

    python -m src.data.team_resolution review     # unresolved names from the current dataset
    python -m src.data.team_resolution suggest    # same, with ranked suggestions
    python -m src.data.team_resolution approve --raw "Ipswich" --team-id ENG_ipswich_town
    python -m src.data.team_resolution register-team --team-id ENG_x --name "X FC" --country ENG
    python -m src.data.team_resolution validate   # integrity of configs/team_aliases.yaml

Nothing is ever mapped automatically: `approve` is an explicit, attributed human decision.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.cli_utils import configure_output
from src.config import config_dir_for, load_config

from .clean import SOURCE_NAME
from .dataset import DatasetError, resolve_dataset
from .teams import Alias, TeamDirectory

ROOT = Path(__file__).resolve().parents[2]


def _queue(root: Path) -> dict[str, dict]:
    data_cfg = load_config("data", config_dir_for(root))
    try:
        ref = resolve_dataset(root / data_cfg.processed_dir)
    except DatasetError:
        return {}
    mapping = json.loads((ref.path / "team_mapping.json").read_text(encoding="utf-8"))
    return mapping.get("review_queue", {})


def _failed_queue(root: Path) -> dict[str, dict]:
    data_cfg = load_config("data", config_dir_for(root))
    failed = root / data_cfg.processed_dir / "last_failed_quality_report.json"
    if not failed.exists():
        return {}
    rep = json.loads(failed.read_text(encoding="utf-8"))
    return {f"failed|{t['raw_name']}": t for t in rep.get("unmatched_teams", [])}


def main(argv: list[str] | None = None) -> int:
    configure_output()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("review")
    sub.add_parser("suggest")
    sub.add_parser("validate")
    ap = sub.add_parser("approve")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--team-id", required=True)
    ap.add_argument("--source", default=SOURCE_NAME)
    ap.add_argument("--valid-from", default=None)
    ap.add_argument("--valid-to", default=None)
    ap.add_argument("--approved-by", default="manual")
    ap.add_argument("--confidence", type=float, default=1.0)
    rp = sub.add_parser("register-team")
    rp.add_argument("--team-id", required=True)
    rp.add_argument("--name", required=True)
    rp.add_argument("--country", required=True)
    a = p.parse_args(argv)

    root = Path(a.root)
    alias_file = config_dir_for(root) / "team_aliases.yaml"
    directory = TeamDirectory.load(alias_file)

    if a.cmd in ("review", "suggest"):
        queue = {**_queue(root), **_failed_queue(root)}
        if not queue:
            print("review queue is empty")
            return 0
        for entry in sorted(queue.values(), key=lambda e: e["raw_name"]):
            print(
                f"UNRESOLVED {entry['raw_name']!r} ({entry.get('country')}) "
                f"first seen {entry.get('first_seen')}"
            )
            if a.cmd == "suggest":
                for s in entry.get("suggestions", []) or ["(no similar known team)"]:
                    print(f"    suggestion: {s}")
        return 1  # non-zero: there is work to do
    if a.cmd == "validate":
        problems = directory.validate()
        for pr in problems:
            print("PROBLEM:", pr)
        print(f"{len(directory.teams)} teams, {len(directory.aliases)} aliases, {len(problems)} problems")
        return 1 if problems else 0
    if a.cmd == "register-team":
        if a.team_id in directory.teams:
            print(f"team {a.team_id} already exists", file=sys.stderr)
            return 2
        directory.teams[a.team_id] = {
            "team_id": a.team_id,
            "canonical_name": a.name,
            "country": a.country,
        }
        problems = directory.validate()
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 2
        directory.dump(alias_file)
        print(f"registered {a.team_id}")
        return 0
    if a.cmd == "approve":
        if a.team_id not in directory.teams:
            print(f"unknown team_id {a.team_id}; use register-team first", file=sys.stderr)
            return 2
        directory.aliases.append(
            Alias(
                a.source,
                a.raw,
                a.team_id,
                date.fromisoformat(a.valid_from) if a.valid_from else None,
                date.fromisoformat(a.valid_to) if a.valid_to else None,
                "fuzzy_approved" if a.confidence < 1.0 else "manual",
                a.approved_by,
                a.confidence,
            )
        )
        problems = directory.validate()
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 2
        directory.dump(alias_file)
        print(f"approved alias {a.raw!r} -> {a.team_id}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
