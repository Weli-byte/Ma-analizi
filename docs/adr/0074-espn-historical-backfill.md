# ADR 0074 - Europa / Conference League 2025-26 results backfilled from ESPN

Date: 2026-10-24. Status: accepted. Builds on ADR 0070 (ESPN fixtures) and ADR 0058 (live results store).

## Context
The public-domain openfootball files have no 2025-26 league phase for the Europa League and the Conference League, so those two competitions lacked a season of history, and clubs seen only there had little or none. Nothing may be paid for. ESPN's keyless scoreboard (unofficial, RESEARCH_ONLY like ADR 0070) answers one day per request.

## Decision
* `python -m src.markets.espn_backfill` scans a window (default 2025-07-01 to 2026-07-01) day by day and adds finished matches to the live results store. The cloud job runs it with `--if-needed`: a marker `espn_backfill_done` in each store makes later runs no-ops, and a window with a failed day is not marked done (the merge is idempotent, so the repeat is safe).
* Only 90-minute scores are added: league-phase matches always; a knockout-month match (February to June) only when ESPN says `STATUS_FULL_TIME`. Extra-time and penalty matches stay out (their 90-minute score is not in the feed). The live collection keeps refusing knockout-month scores.
* A club that does not resolve in the team directory is listed, never guessed or registered. Nine exact aliases of existing clubs were added (FC Basel, Ludogorets Razgrad, Maccabi Tel-Aviv, PAOK, Racing Genk, Young Boys, Legia Warsaw, Mainz, Rapid Vienna; approver string `claude-assistant 2026-10-24 ... owner to re-confirm`). Eight clubs stay unresolved (Breidablik, Drita Gjilan, Dynamo Kyiv, Hamrun Spartans, KF Shkendija, Lausanne Sports, Shelbourne, Sigma Olomouc).
* Availability: `first_seen` is the day the backfill ran. A backfilled result is never presented as available earlier, so no historical backtest can use it retroactively. A backfilled row carries its own `season` (the store-level label is the current season).

## Result (local run 2026-10-24)
Europa League 184 results and Conference League 180 results added for 2025-26 (the league phase and the knockout matches decided in 90 minutes), 7 knockout matches left out because they went to extra time or penalties.

## Limits
Unofficial source, terms unverified (research only, so are all models fitted on it). ESPN could change or withdraw the feed. A team's name spelling differs between sources: unresolved names reduce coverage, they never create wrong data.
