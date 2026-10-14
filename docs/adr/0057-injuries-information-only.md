# ADR 0057: absent players shown as information only

Date: 2026-10-14. Status: accepted.

## Decision
Injuries and suspensions are collected from the API-Football FREE plan (`/injuries?date=`; verified 2026-10-14 with the
owner's key: 168 Champions League entries for one day, one call covers every league) and shown on the match page as an
"Eksikler" card. They NEVER enter a probability: there is no historical injury data to calibrate an effect on, so a
weight would be a guess (project rule: no fake or imputed data).

## Constraints, stated on the page
- The free plan only answers dates in [yesterday, tomorrow]; the card says so when nothing was observed and never reads
  "nobody is out" without an observation. An empty side says "no absence recorded in the source".
- No report time exists per entry: `observed_at` is our fetch time and is shown. Each stored file is immutable and carries the
  SHA-256 of the raw response; an unchanged response is not stored again.
- 100 requests/day: two calls (today, tomorrow) per 4-hour cloud run, about 12/day.
- A club the directory cannot resolve is dropped and counted (`unresolved_clubs`), never guessed.
- Status mapping: `Questionable` = doubtful, a reason with suspension/card = suspended, non-medical reasons (roster, national
  team, personal) = not in the squad, everything else = injured.
- Terms of the free plan for commercial use are unverified (RESEARCH_ONLY, as for every provider here).

## Where
`src/markets/injuries.py`, collected by `markets-cloud.yml` into `artifacts/injuries/<day>/` on the data branch (3 days kept),
synced to the PC by `python -m src.markets.run --sync-remote`, rendered by `pages.injuries_card`.

## Not done
Market values (no licensed live source, ADR 0056) and any effect of absences on the forecast.
