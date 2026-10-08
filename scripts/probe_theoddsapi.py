"""Prints the REAL shape of The Odds API responses (no key in the output) so the adapter is written
against what the provider actually returns. Run in CI (the API host is blocked on some ISPs):
workflow live-ai.yml. Costs 1 request for /sports (free) and 1 odds request."""

import json
import os
import urllib.parse
import urllib.request

BASE = "https://api.the-odds-api.com/v4"
key = os.environ["THE_ODDS_API_KEY"]


def get(path: str, **params):
    q = urllib.parse.urlencode({**params, "apiKey": key})
    with urllib.request.urlopen(f"{BASE}{path}?{q}", timeout=30) as r:  # noqa: S310 - fixed https host
        return json.loads(r.read().decode()), dict(r.headers)


sports, h = get("/sports/")
soccer = [s for s in sports if s["key"].startswith("soccer")]
print(
    "soccer sport keys:",
    sorted(
        s["key"]
        for s in soccer
        if s["key"] in ("soccer_epl", "soccer_spain_la_liga") or "epl" in s["key"] or "la_liga" in s["key"]
    ),
)
print("headers:", {k: v for k, v in h.items() if k.lower().startswith("x-requests")})
events, h2 = get(
    "/sports/soccer_epl/odds/", regions="uk,eu", markets="h2h", oddsFormat="decimal", dateFormat="iso"
)
print(
    "events:", len(events), "| headers:", {k: v for k, v in h2.items() if k.lower().startswith("x-requests")}
)
if events:
    e = events[0]
    print("event keys:", sorted(e.keys()))
    b = e["bookmakers"][0]
    print("bookmaker keys:", sorted(b.keys()), "| market keys:", sorted(b["markets"][0].keys()))
    print(json.dumps({**e, "bookmakers": e["bookmakers"][:1]}, indent=1)[:2500])


# team names exactly as the provider spells them (for TeamDirectory aliases), both leagues
for sport in ("soccer_epl", "soccer_spain_la_liga"):
    ev, _ = get(
        f"/sports/{sport}/odds/", regions="uk,eu", markets="h2h", oddsFormat="decimal", dateFormat="iso"
    )
    names = sorted({t for e in ev for t in (e["home_team"], e["away_team"])})
    print(sport, "events", len(ev), "teams", names)
    books = sorted({b["title"] for e in ev for b in e["bookmakers"]})
    print(sport, "bookmakers", books)
