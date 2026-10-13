"""`python -m src.markets.cup_clubs [--apply]`: clubs of the European cups (ADR 0055).

For every (club name, country) in the openfootball cup files: a club that already exists in the directory (domestic clubs of our
six leagues, resolved through their openfootball aliases) gets an alias for the source `openfootball-cups`; every other club is
CREATED once, with the id `<COUNTRY>_<slug>` and the country given in the file. The creation is deliberate and limited to this
source (clubs of other countries have no other identity in the project); a club that appears under two spellings becomes two
clubs (a documented limitation). Re-running changes nothing.
"""

import argparse
import re
import unicodedata
from pathlib import Path

from src.config import config_dir_for
from src.data.teams import Alias, TeamDirectory, _loose, normalize_name
from src.ingestion.openfootball_cups import COMPETITIONS, FIRST_SEASON, LAST_SEASON, SOURCE, fetch, parse

ROOT = Path(__file__).resolve().parents[2]
SAME_CLUB = {
    ("Galatasaray SK", "TUR"): "TUR_galatasaray"
}  # spelled differently in the cup files than in the league files
NOTE = "claude-assistant 2026-10-13 (club of the openfootball cup files; created or linked automatically; owner to re-confirm)"


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def cup_names() -> dict[tuple[str, str], int]:
    """(name, country) -> number of cup matches, over every season the repository has."""
    count: dict[tuple[str, str], int] = {}
    for comp in COMPETITIONS:
        for year in range(FIRST_SEASON[comp], LAST_SEASON[comp] + 1):
            for m in parse(fetch(comp, year).decode("utf-8"), comp, year):
                for key in ((m.home, m.home_cc), (m.away, m.away_cc)):
                    count[key] = count.get(key, 0) + 1
    return count


def plan(directory: TeamDirectory, names: dict[tuple[str, str], int]) -> tuple[list[tuple], list[tuple]]:
    linked, created = [], []
    for (name, cc), _n in sorted(names.items()):
        if directory.resolve(SOURCE, name, cc).team_id is not None:
            continue  # already known for this source
        existing = directory.resolve("openfootball", name, cc)
        if (name, cc) in SAME_CLUB and SAME_CLUB[(name, cc)] in directory.teams:
            linked.append((name, cc, SAME_CLUB[(name, cc)]))
            continue
        if existing.team_id is not None:
            linked.append((name, cc, existing.team_id))
        else:
            created.append((name, cc, ""))
    # spellings that differ only by club-form words ("Boavista" / "Boavista FC") are ONE new club
    by_key: dict[tuple[str, str], list[str]] = {}
    for name, cc, _ in created:
        by_key.setdefault((cc, _loose(normalize_name(name)) or normalize_name(name)), []).append(name)
    created = [
        (
            name,
            cc,
            f"{cc}_{slugify(min(by_key[(cc, _loose(normalize_name(name)) or normalize_name(name))], key=len))}",
        )
        for name, cc, _ in created
    ]
    return linked, created


def domestic_names() -> dict[tuple[str, str], int]:
    """(name, country) of the history-only domestic leagues (ADR 0056) -> matches, over the loaded seasons."""
    import urllib.request

    from src.ingestion.endpoints import endpoint
    from src.ingestion.openfootball import ALL_FILES, BASE, HISTORY_ONLY, parse, season_dir
    from src.mlops.oplog import logged_urlopen

    count: dict[tuple[str, str], int] = {}
    for league, (code, _tz, _cc) in HISTORY_ONLY.items():
        for year in range(2019, 2026):
            url = f"{endpoint('OPENFOOTBALL_BASE_URL', BASE)}/{season_dir(year)}/{code}.json"
            try:
                raw = logged_urlopen("openfootball", "fixtures", urllib.request.Request(url), 30.0)
            except Exception:  # noqa: BLE001 - a season file the source does not have
                continue
            for f in parse(raw, league, season_dir(year)):
                for name in (f.home_team_raw_name, f.away_team_raw_name):
                    count[(name, ALL_FILES[league][2])] = count.get((name, ALL_FILES[league][2]), 0) + 1
    return count


def plan_domestic(directory: TeamDirectory, names: dict[tuple[str, str], int]) -> tuple[list, list]:
    """Names of the domestic files: known -> skip; same country and a unique (loose) match with a club we already
    have (usually created from the cup files) -> alias for `openfootball`; else a new club."""
    linked, created = [], []
    srcs = (SOURCE, "openfootball")
    for (name, cc), _n in sorted(names.items()):
        if directory.resolve("openfootball", name, cc).team_id is not None:
            continue
        hit = directory.resolve_any_country(srcs, name, country=cc)
        if hit.team_id is not None:
            linked.append((name, cc, hit.team_id))
        else:
            created.append((name, cc, ""))

    # spellings that differ only by club-form words ("Boavista" / "Boavista FC") are ONE new club
    def key(name: str, cc: str) -> tuple[str, str]:
        return cc, _loose(normalize_name(name)) or normalize_name(name)

    by_key: dict[tuple[str, str], list[str]] = {}
    for name, cc, _ in created:
        by_key.setdefault(key(name, cc), []).append(name)
    created = [(name, cc, f"{cc}_{slugify(min(by_key[key(name, cc)], key=len))}") for name, cc, _ in created]
    return linked, created


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--domestic", action="store_true", help="the history-only domestic leagues (ADR 0056)")
    a = ap.parse_args(argv)
    path = config_dir_for(Path(a.root)) / "team_aliases.yaml"
    d = TeamDirectory.load(path)
    if a.domestic:
        linked, created = plan_domestic(d, domestic_names())
    else:
        linked, created = plan(d, cup_names())
    alias_source = "openfootball" if a.domestic else SOURCE
    print(f"{len(linked)} clubs linked to existing ones, {len(created)} new clubs")
    for name, cc, tid in linked[:60]:
        print(f"LINK   {cc} {name!r} -> {tid}")
    for name, cc, tid in created[:15]:
        print(f"CREATE {cc} {name!r} -> {tid}")
    if a.apply:
        made: set[str] = set()
        for name, cc, tid in created:
            if tid in d.teams and tid not in made and d.teams[tid]["canonical_name"] != name:
                tid = f"{tid}_{len(d.teams)}"  # same slug, different club: keep both
            d.teams.setdefault(tid, {"team_id": tid, "canonical_name": name, "country": cc})
            made.add(tid)
            d.aliases.append(Alias(alias_source, name, tid, None, None, "cup_import", NOTE, 0.9))
        for name, _cc, tid in linked:
            d.aliases.append(Alias(alias_source, name, tid, None, None, "cup_import", NOTE, 0.9))
        problems = d.validate()
        if problems:
            print("validation problems:", problems[:3])
            return 1
        d.dump(path)
        print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
