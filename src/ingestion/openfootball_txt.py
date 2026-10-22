"""openfootball Football.TXT league files (CC0 public domain) -> the football.json shape the rest of the project already parses.

Why this exists (2026-10-22): `openfootball/football.json` has no Süper Lig file for 2026-27 (HTTP 404), but the source repositories `openfootball/europe` (turkey, france, ...)
publish `2026-27_tr1.txt`. Same public-domain dedication (CC0 1.0 in the repository's LICENSE.md, read 2026-10-22). This module reads that text format and produces exactly the
`{"matches": [{"round", "date", "time", "team1", "team2", "score": {"ft": [h, a]}}]}` document of football.json, so `openfootball.parse` (time zones, status, ids) does the rest.

Two layouts exist in these repositories and both are read (checked on real files): `HH:MM  Home  H-A (h-a)  Away` / `Home   v   Away` (Süper Lig 2026-27) and
`HH:MM  Home   v Away    H-A (h-a)` (older files). Both have a header, round lines (`▪ Regular Season - N`, `▪ 1. Round`, `▪ Matchday 1`), date lines (`Fri Aug 14 2026`, later ones
without the year), the time given once for the following lines of that day until a new time appears, and indented goal-scorer lines that are ignored. Nothing is guessed: a line that does not parse as a date, a round or a match is skipped and counted."""

import json
import re
import urllib.request
from datetime import date
from pathlib import Path

from src.mlops.oplog import logged_urlopen

from .provider import ProviderError

REPO_BASE = "https://raw.githubusercontent.com/openfootball"
SOURCE = "openfootball-europe-txt"
MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
DATE_RE = re.compile(r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$")
ROUND_RE = re.compile(r"^▪\s*(.+?)\s*$")
MATCH_RE = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>\S.*?)\s{2,}(?:(?P<h>\d+)-(?P<a>\d+)(?:\s*\(\d+-\d+\))?|v)\s{2,}(?P<away>\S.*?)\s*$"
)
# older layout of the same repositories: "HH:MM  Home   v Away    H-A (h-a)" (the result, if any, comes AFTER the away team)
MATCH_V_RE = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}[:.]\d{2})\s+)?(?P<home>\S.*?)\s+v\s+(?P<away>\S.*?)(?:\s{2,}(?P<h>\d+)-(?P<a>\d+)(?:\s*\(\d+-\d+\))?)?\s*$"
)
# repository file of a competition: (repo, path pattern)
FILES = {
    "TUR1": ("europe", "turkey/{season}_tr1.txt", "tr.1"),
    "FRA1": ("europe", "france/{season}_fr1.txt", "fr.1"),
}


def parse_txt(text: str) -> tuple[dict, dict]:
    """(football.json-shaped document, report). Years of the later date lines are inferred from the last full date and the month order."""
    matches, skipped = [], 0
    rnd, year, last_month, cur_date, cur_time = None, None, None, None, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", "=", "(")):
            continue
        r = ROUND_RE.match(line)
        if r:
            rnd = r.group(1)
            continue
        d = DATE_RE.match(line)
        if d:
            mon, day = MONTHS[d.group(1)], int(d.group(2))
            if d.group(3):
                year = int(d.group(3))
            elif year is None:
                skipped += 1
                continue
            elif last_month is not None and mon < last_month:
                year += 1  # December -> January
            last_month = mon
            cur_date, cur_time = date(year, mon, day), None
            continue
        m = MATCH_RE.match(line) or MATCH_V_RE.match(line)
        if m and cur_date is not None:
            if m.group("time"):
                cur_time = m.group("time").replace(".", ":")
            row = {"round": rnd, "date": cur_date.isoformat(), "team1": m.group("home").strip(), "team2": m.group("away").strip()}
            if cur_time:
                row["time"] = cur_time.zfill(5)
            if m.group("h") is not None:
                row["score"] = {"ft": [int(m.group("h")), int(m.group("a"))]}
            matches.append(row)
        elif not line.startswith(" " * 12):  # scorer continuation lines are deeply indented; anything else that fails is counted
            skipped += 1
    scored = sum(1 for x in matches if "score" in x)
    return {"matches": matches}, {"matches": len(matches), "scored": scored, "skipped_lines": skipped}


def url_for(league: str, season_start_year: int) -> str:
    repo, pattern, _ = FILES[league]
    return f"{REPO_BASE}/{repo}/master/{pattern.format(season=f'{season_start_year}-{str(season_start_year + 1)[2:]}')}"


def fetch_to_cache(root: Path, league: str, season_start_year: int, timeout: float = 30.0) -> dict:
    """Download one season file (read-only, public domain) and write the football.json-shaped document under the OPERATIONAL cache. The frozen research snapshot
    (`artifacts/markets/_cache`) is never touched, so every committed Sprint 1 to 5 result stays reproducible."""
    url = url_for(league, season_start_year)
    try:
        raw = logged_urlopen(SOURCE, "fixtures", urllib.request.Request(url), timeout)
    except Exception as e:  # noqa: BLE001 - re-raised as the provider error type, name only
        raise ProviderError(f"{SOURCE}: {type(e).__name__}") from None
    doc, rep = parse_txt(raw.decode("utf-8-sig"))
    code = FILES[league][2]
    season = f"{season_start_year}-{str(season_start_year + 1)[2:]}"
    out = root / "artifacts" / "league_models" / "operational_cache" / SOURCE / season / f"{code}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    import hashlib

    return {"league": league, "season": season, "url": url, "path": str(out.relative_to(root)), "sha256_of_source": hashlib.sha256(raw).hexdigest(), **rep}


def main(argv=None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Fetch an openfootball Football.TXT league season into the operational cache")
    ap.add_argument("--league", required=True, choices=sorted(FILES))
    ap.add_argument("--season", required=True, type=int, help="start year, e.g. 2026")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    a = ap.parse_args(argv)
    sys.stdout.write(json.dumps(fetch_to_cache(Path(a.root), a.league, a.season), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
