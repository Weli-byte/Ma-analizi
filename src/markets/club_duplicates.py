"""`python -m src.markets.club_duplicates [--apply OLD=NEW ...]`: possible duplicate clubs, for the OWNER to approve.

A club the sources spell two ways becomes two clubs in the directory: each has half of the history and half of the
rating. This lists pairs of the same country whose names are alike (club-form words removed, one name contained in
the other, or high text similarity) and drops pairs that ever played EACH OTHER (two clubs that met are different
clubs, e.g. Dundee and Dundee United). Nothing is merged without `--apply OLD=NEW`: it retargets every alias of OLD
to NEW and removes OLD (ADR 0056).
"""

import argparse
import difflib
from dataclasses import replace
from pathlib import Path

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory, _loose, normalize_name

ROOT = Path(__file__).resolve().parents[2]


def _tokens(norm: str) -> list[str]:
    return [t for t in _loose(norm).split() if t]


def reason(a: str, b: str) -> str | None:
    na, nb = normalize_name(a), normalize_name(b)
    if _loose(na) == _loose(nb):
        return "same name apart from club-form words"
    ta, tb = _tokens(na), _tokens(nb)
    if ta and tb:
        short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(short) < len(long_) and long_[: len(short)] == short:
            return "one name is the start of the other"
    if difflib.SequenceMatcher(None, na, nb).ratio() >= 0.86:
        return "very similar text"
    return None


def propose(directory: TeamDirectory, met: set[frozenset], matches: dict[str, int]) -> list[dict]:
    by_country: dict[str, list[dict]] = {}
    for t in directory.teams.values():
        by_country.setdefault(t["country"], []).append(t)
    out = []
    for cc, teams in sorted(by_country.items()):
        for i, x in enumerate(teams):
            for y in teams[i + 1 :]:
                if frozenset((x["team_id"], y["team_id"])) in met:
                    continue  # they played each other: two clubs
                why = reason(x["canonical_name"], y["canonical_name"])
                if why:
                    keep, drop = sorted((x, y), key=lambda t: -matches.get(t["team_id"], 0))
                    out.append(
                        {
                            "country": cc,
                            "keep": keep["team_id"],
                            "keep_name": keep["canonical_name"],
                            "keep_matches": matches.get(keep["team_id"], 0),
                            "drop": drop["team_id"],
                            "drop_name": drop["canonical_name"],
                            "drop_matches": matches.get(drop["team_id"], 0),
                            "why": why,
                        }
                    )
    return out


def merge(directory: TeamDirectory, drop: str, keep: str) -> int:
    """Retarget every alias of `drop` to `keep` and remove `drop`; returns the number of aliases moved."""
    if drop not in directory.teams or keep not in directory.teams:
        raise KeyError(f"unknown club id in {drop}={keep}")
    if directory.teams[drop]["country"] != directory.teams[keep]["country"]:
        raise ValueError("clubs of different countries are never merged")
    moved = 0
    for i, a in enumerate(directory.aliases):
        if a.team_id == drop:
            directory.aliases[i] = replace(a, team_id=keep)
            moved += 1
    directory.teams.pop(drop)
    return moved


def render(rows: list[dict]) -> str:
    lines = [
        "# Possible duplicate clubs (for approval)",
        "",
        "Pairs of the same country that look like one club. Pairs that played each other are already removed.",
        "To merge a pair run `python -m src.markets.club_duplicates --apply DROP_ID=KEEP_ID`.",
        "",
        "| Country | Keep (matches) | Merge into it (matches) | Why |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['country']} | {r['keep_name']} `{r['keep']}` ({r['keep_matches']}) "
            f"| {r['drop_name']} `{r['drop']}` ({r['drop_matches']}) | {r['why']} |"
        )
    return "\n".join(lines) + "\n"


def record_merges(path: Path, pairs: list[tuple[str, str]]) -> None:
    """Append applied merges to `club_id_merges.yaml`, so forecasts stored under an old id are still joined to their results (ADR 0090)."""
    import yaml

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
    data = yaml.safe_load(text) or {"merges": []}
    known = {m["old"] for m in data["merges"]}
    data["merges"] += [{"old": o, "new": n, "approved": "owner, club_duplicates --apply"} for o, n in pairs if o not in known]
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--apply", nargs="*", metavar="DROP=KEEP")
    a = ap.parse_args(argv)
    root = Path(a.root)
    cdir = config_dir_for(root)
    path = cdir / "team_aliases.yaml"
    d = TeamDirectory.load(path)
    if a.apply:
        for pair in a.apply:
            drop, keep = pair.split("=")
            print(f"{drop} -> {keep}: {merge(d, drop, keep)} alias(es) moved")
        problems = d.validate()
        if problems:
            print("validation problems:", problems[:3])
            return 1
        d.dump(path)
        record_merges(cdir / "club_id_merges.yaml", [tuple(p.split("=")) for p in a.apply])
        return 0
    from .of_source import load_world

    world = load_world(root, d, load_config("markets", cdir))
    met = {frozenset((m.home_id, m.away_id)) for m in world.history}
    matches: dict[str, int] = {}
    for m in world.history:
        for t in (m.home_id, m.away_id):
            matches[t] = matches.get(t, 0) + 1
    rows = propose(d, met, matches)
    out = root / "reports" / "club_duplicates.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows), encoding="utf-8")
    print(f"{len(rows)} possible duplicate pair(s) written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
