"""`python -m src.markets.alias_tool [--apply]`: map the club names found in the Highlightly store to our clubs.

Names that already resolve are left alone. For the rest the closest canonical club of the SAME country is proposed
and accepted only when it is clearly the best (similarity >= 0.6 and ahead of the runner-up by 0.1, or the only
candidate); everything else is listed for a human. Nothing is auto-registered as a new club. Re-run it whenever the
collection has fetched more seasons (relegated/promoted clubs appear then).
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from src.config import config_dir_for
from src.data.teams import Alias, TeamDirectory, normalize_name
from src.ingestion.openfootball import FILES

from .stats_store import store_dir

ROOT = Path(__file__).resolve().parents[2]
APPROVER = "claude-assistant (closest club of the same country in the Highlightly store; owner to re-confirm)"


def store_names(root: Path) -> dict[str, set[str]]:
    names: dict[str, set[str]] = defaultdict(set)
    base = store_dir(root)
    for p in sorted(base.glob("*_*.jsonl")) if base.exists() else []:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                names[r["league"]].update([r["home"]["name"], r["away"]["name"]])
    return names


def propose(directory: TeamDirectory, names: dict[str, set[str]]) -> tuple[list[tuple], list[tuple]]:
    accepted, manual = [], []
    for league, ns in sorted(names.items()):
        country = FILES[league][2]
        for n in sorted(ns):
            if directory.resolve("highlightly", n, country).team_id is not None:
                continue
            sug = directory.review_queue.get(f"highlightly|{country}|{normalize_name(n)}", {}).get(
                "suggestions", []
            )
            top = sug[0] if sug else None
            clear = (
                top
                and top["similarity"] >= 0.6
                and (len(sug) == 1 or top["similarity"] - sug[1]["similarity"] >= 0.1)
            )
            (accepted if clear else manual).append(
                (
                    league,
                    country,
                    n,
                    top["team_id"] if top else None,
                    top["similarity"] if top else 0.0,
                    sug[:3],
                )
            )
    return accepted, manual


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument(
        "--apply", action="store_true", help="write the accepted aliases to configs/team_aliases.yaml"
    )
    a = ap.parse_args(argv)
    root = Path(a.root)
    path = config_dir_for(root) / "team_aliases.yaml"
    d = TeamDirectory.load(path)
    accepted, manual = propose(d, store_names(root))
    for league, _, n, tid, sim, _ in accepted:
        print(f"ACCEPT {league:10} {n!r} -> {tid} ({sim})")
    for league, _, n, _tid, _sim, sug in manual:
        print(f"MANUAL {league:10} {n!r} suggestions {[(s['team_id'], s['similarity']) for s in sug]}")
    if a.apply:
        for _, _, n, tid, _, _ in accepted:
            d.aliases.append(Alias("highlightly", n, tid, None, None, "fuzzy_approved", APPROVER, 0.9))
        problems = d.validate()
        if problems:
            print("validation problems:", problems[:3])
            return 1
        d.dump(path)
        print(f"{len(accepted)} alias(es) written; {len(manual)} left for manual review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
