"""Club logos for the LOCAL alias review (standard library only, runs on a GitHub runner).

Two modes:
- `--stats DIR --out DIR`: download the logos of every team id in the Highlightly store.
- `--search LIST.json --out DIR [--max-requests N]`: for clubs the store has never seen (relegated / older clubs) look the
  club up by name with `GET /teams?name=` (verified 2026-10-12: returns id, name and logo), trying a few spellings, and
  download the logo. The result is `_search.json` (team id -> Highlightly id and the name it was found under) so the review
  page can show "found as ..." next to the logo. Clubs that are not found or are ambiguous are listed, never guessed.
Logos are club trademarks: identification use only (Highlightly terms 6.3), kept in the private data repository, never
published.
"""

import argparse
import json
import os
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

LOGO = "https://highlightly.net/soccer/images/teams"
API = "https://soccer.highlightly.net"
UA = {"User-Agent": "Mozilla/5.0 (alias review)"}
# spellings Highlightly may use; tried in order after the canonical name (each try costs one request)
VARIANTS = {
    "West Bromwich Albion": ["West Brom"],
    "West Ham United": ["West Ham"],
    "Hellas Verona": ["Verona"],
    "Saint-Étienne": ["Saint Etienne", "St Etienne", "AS Saint-Etienne"],
    "St. Pauli": ["FC St. Pauli", "St Pauli"],
    "Darmstadt": ["Darmstadt 98", "SV Darmstadt 98"],
    "Heidenheim": ["1. FC Heidenheim", "FC Heidenheim"],
    "VfL Bochum": ["Bochum"],
    "Clermont Foot": ["Clermont"],
    "Nîmes": ["Nimes", "Nimes Olympique"],
    "Leganes": ["Leganés", "CD Leganes"],
    "Almeria": ["Almería", "UD Almeria"],
    "Cadiz": ["Cádiz", "Cadiz CF"],
    "Mallorca": ["Real Mallorca", "RCD Mallorca"],
    "Real Valladolid": ["Valladolid"],
    "Las Palmas": ["UD Las Palmas"],
    "Fortuna Düsseldorf": ["Fortuna Dusseldorf"],
    "Arminia Bielefeld": ["Bielefeld"],
    "SPAL": ["SPAL 2013", "Spal"],
    "Fatih Karagümrük": ["Fatih Karagumruk", "Karagumruk"],
    "MKE Ankaragücü": ["MKE Ankaragucu", "Ankaragucu"],
    "Hertha BSC": ["Hertha Berlin", "Hertha"],
    "Norwich City": ["Norwich City FC", "Norwich"],
    "Wolverhampton Wanderers": ["Wolves", "Wolverhampton", "Wolverhampton Wanderers FC"],
    "AC Ajaccio": ["Ajaccio", "AC Ajaccio Corsica"],
    "Montpellier": ["Montpellier HSC", "Montpellier Herault"],
    "Greuther Fürth": ["Greuther Furth", "SpVgg Greuther Furth", "Fürth", "Furth"],
}


def logo_url(tid: int) -> str:
    base = os.environ.get("HIGHLIGHTLY_LOGO_BASE_URL")  # tests: a loopback server only
    if base and not base.startswith(("http://127.0.0.1", "http://localhost")):
        raise ValueError("HIGHLIGHTLY_LOGO_BASE_URL may only point to a loopback address")
    return f"{base or LOGO}/{tid}.png"


def team_ids(stats_dir: Path) -> set[int]:
    ids: set[int] = set()
    for p in sorted(stats_dir.glob("*_*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                ids.update((r["home"]["id"], r["away"]["id"]))
    return ids


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


def spellings(name: str) -> list[str]:
    out = [name, *VARIANTS.get(name, [])]
    for tail in (" City", " Town"):  # "Leicester City" -> "Leicester"
        if name.endswith(tail):
            out.append(name[: -len(tail)])
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def search_club(name: str, get, budget: list[int]) -> tuple[dict | None, str]:
    """(match, status). `get(name)` returns the list of {id, name, logo} for one query; `budget[0]` counts down."""
    for q in spellings(name):
        if budget[0] <= 0:
            return None, "budget exhausted"
        budget[0] -= 1
        results = get(q)
        exact = [r for r in results if _norm(r["name"]) == _norm(q)]
        pick = exact or (results if len(results) == 1 else [])
        if len(pick) == 1:
            return {"id": pick[0]["id"], "name": pick[0]["name"], "query": q}, "found"
        if len(results) > 1 and not exact:
            return None, f"ambiguous for {q!r}: {[r['name'] for r in results][:5]}"
    return None, "not found"


def _api_get(key: str, name: str) -> list[dict]:
    url = f"{API}/teams?{urllib.parse.urlencode({'name': name})}"
    req = urllib.request.Request(url, headers={"x-rapidapi-key": key, **UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https host
        return json.loads(r.read().decode())["data"]


def _download(tid: int, out: Path, failed: list) -> bool:
    dest = out / f"{tid}.png"
    if dest.exists():
        return True
    try:
        with urllib.request.urlopen(urllib.request.Request(logo_url(tid), headers=UA), timeout=20) as r:  # noqa: S310
            dest.write_bytes(r.read())
        return True
    except Exception as e:  # noqa: BLE001 - report and continue
        failed.append((tid, getattr(e, "code", type(e).__name__)))
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stats", help="Highlightly store directory (logos of every team id in it)")
    ap.add_argument("--search", help="JSON list of {team_id, name, country} to look up by name")
    ap.add_argument("--max-requests", type=int, default=70)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    if a.stats:
        for tid in sorted(team_ids(Path(a.stats))):
            ok += _download(tid, out, failed)
    summary = {"downloaded_or_present": ok, "failed": failed[:20], "n_failed": len(failed)}
    if a.search:
        key = os.environ["HIGHLIGHTLY_API_KEY"]
        budget = [a.max_requests]
        prev = out / "_search.json"  # a second pass keeps what the first one found
        found = json.loads(prev.read_text(encoding="utf-8")) if prev.exists() else {}
        notes = {}
        for club in json.loads(Path(a.search).read_text(encoding="utf-8")):
            if club["team_id"] in found and (out / f"{found[club['team_id']]['id']}.png").exists():
                continue
            try:
                match, status = search_club(club["name"], lambda q: _api_get(key, q), budget)
            except Exception as e:  # noqa: BLE001 - e.g. HTTP 429 when the daily quota is used up: stop, report
                notes[club["team_id"]] = f"api error {getattr(e, 'code', type(e).__name__)}: stopped"
                break
            if match and _download(match["id"], out, failed):
                found[club["team_id"]] = match
            else:
                notes[club["team_id"]] = (
                    status if not match else f"found as {match['name']} but the logo download failed"
                )
        (out / "_search.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
        summary.update(
            found=len(found), not_found=notes, requests_left=budget[0], download_failures=failed[:20]
        )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
