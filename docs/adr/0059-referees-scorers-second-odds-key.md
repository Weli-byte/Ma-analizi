# ADR 0059: referees, top scorers, schedule fallback, a second odds key

Date: 2026-10-14. Status: accepted.

## Research (free sources for referee and player data)
- football-data.org free tier (verified 2026-10-14 with the project key): every match object carries `referees` (name, nationality)
  once appointed; `/competitions/{code}/scorers` returns the top scorers (goals, assists, penalties, matches played) for PL, PD, BL1,
  SA, FL1, DED, PPL and CL. No cards, no line-ups, no per-match player statistics on the free tier.
- API-Football free plan: players/line-ups only for seasons 2022-2024 (not the running season). Not usable for current data.
- Transfermarkt-derived datasets: scraped, terms unclear, updates paused (ADR 0056). Not used.
- Other sites (Sofascore, FotMob, WhoScored): no public licence for reuse; not scraped.

## Decisions
- `live_results` also stores, per competition: the referee of every finished match, the upcoming fixtures with the referee once named,
  and the top-60 scorers. Two more requests per competition per run, paced for the 10-per-minute limit, one retry after a 429.
- The match page gets two tabs: **Hakem** (name, nationality, this season's matches in our results: count, goals per match,
  home/draw/away share, over 2.5, last five) and **Oyuncular** (each team's top scorers per competition). Information only, never in a
  probability; small samples are labelled as such; leagues the source does not cover say so.
- The Champions League schedule from football-data.org completes the odds-store fixture list, so its forecasts continue when the odds
  provider is unavailable.
- **A second free The Odds API key** (`THE_ODDS_API_KEY_2`): keys are used in order; a key that answers 401 "quota reached" is paused
  for 6 hours and the next key is used; the reserve applies per key; files name a key by a hash, never by the key. Without any working
  key the odds-driven parts stop (exact odds, value suggestions, Süper Lig / Europa League / Conference League fixtures and results);
  the model forecasts for the other competitions continue.

## Club and competition logos (added the same day)
The free football-data.org match lists carry each club's crest URL and the competition emblem. They are stored per competition
and downloaded (only from `crests.football-data.org`) into `artifacts/crests/` on the private data branch; the web app uses them
for clubs the Highlightly logos do not cover (e.g. LASK, Feyenoord, Porto) and for the Champions League emblem. Europa / Conference
League clubs outside these competitions still depend on the Highlightly name search, which now also tries the name without
club-form words ("FC Porto" -> "Porto") and an ASCII spelling. Logos remain trademarks: shown only with `FORECAST_UI_LOGOS=1`.
