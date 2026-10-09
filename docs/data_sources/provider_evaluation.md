# Provider evaluation for commercial use (Phase O)

Reviewed 2026-10-08. **Not legal advice.** "Verified" = read on the provider's own page or observed with a real
call on that date. Nothing below is a licence grant; every source stays `RESEARCH_ONLY` until the owner holds
written permission.

| Provider | Used for | Verified (2026-10-08) | NOT verified | Commercial status |
|---|---|---|---|---|
| football-data.co.uk | historical results + closing odds CSV | no reuse terms found (see licensing.md) | permission | RESEARCH_ONLY; REMOVED from the product path (ADR 0044) |
| football-data.org | fixtures, results, live status | pricing page: Free 10 calls/min (12 competitions); paid tiers EUR 12 (livescores), 29, 49, 99, 199 /month; Odds add-on EUR 15, Statistics add-on EUR 15. The pricing page says nothing on commercial use or redistribution and points to the operator by email. `/terms` returns 404. | licence terms for any tier | **CONDITIONAL** after reading the full terms 2026-10-09 (licensing.md): no commercial ban; attribution + single-app key + delete-on-cancel; ask the operator to confirm in one line |
| API-Football (api-sports) | current injuries (free plan, [yesterday, tomorrow]) | real calls: Free plan 100 req/day, 10/min; seasons 2022-2024 only. The terms and pricing pages returned HTTP 403 to automated fetch | terms, paid-plan commercial rights | **NO PUBLICATION LICENCE** (terms read 2026-10-09): internal use only, no player-level data in public outputs; see licensing.md |
| The Odds API | exact-timestamp 1X2 odds | real calls: per-market `last_update` present, credits header; the site could not be fetched (TLS error from the fetch tool; the PC network also blocks it, so collection runs on GitHub Actions) | plan prices, redistribution terms | **COMMERCIAL USE PERMITTED for derived products**; no raw redistribution (terms read 2026-10-09) |
| ESPN (public JSON) | rosters/lineups, DraftKings odds (approximate) | undocumented public endpoints, no terms granting reuse | everything | RESEARCH_ONLY, never for product |
| FPL (public JSON) | EPL injuries | undocumented public endpoints | terms | RESEARCH_ONLY |
| OpenLigaDB | Bundesliga live/test data | open community API | licence text | RESEARCH_ONLY until read |
| **openfootball/football.json** | fixtures + results (EPL, La Liga, more leagues exist) | repository README: public domain, "no restrictions whatsoever"; real files fetched and ingested 2026-10-08 | completeness (community contributions); no lineups/injuries/odds | **COMMERCIAL OK (public domain)** for results and fixtures only |
| Transfermarkt | rejected | ToS forbids scraping (transfermarkt_decision.md) | - | NOT USED |

## Free options researched (2026-10-08)
- openfootball: adopted (above), ADR 0038.
- StatsBomb open data: free for research with mandatory attribution and logo; commercial terms are in a LICENSE.pdf that was not read; covers selected historical competitions, not current matches. Not adopted.
- FBref/Understat/Transfermarkt/WhoScored: scraping against ToS. Not used.
No free source with an explicit commercial grant exists for odds, injuries or lineups; those stay research-only or need a paid contract.

## Match statistics (corners, cards, shots) candidates, searched 2026-10-09
| Source | Verified | Status |
|---|---|---|
| Highlightly | terms 6.1: storing and using the data in your apps/products is allowed; no proxying; PRO USD 9.49/month, 7,500 req/day; Basic free 100 req/day under different terms | BEST CANDIDATE, needs a key to verify responses and history |
| TheStatsAPI | USD 50/month, 7-day trial, commercial products allowed | fallback |
| football-data.org Statistics add-on | EUR 15/month; terms silent on commercial use | conditional |
| DataHub copy of football-data.co.uk | labelled PDDL by a re-publisher | not used |

## What a commercial launch needs (owner decisions, in this order)
1. Fixtures + results: openfootball covers this for free. Live scores, injuries, lineups: one paid, contract-backed provider whose written terms allow display and derived
   forecasts (candidates to ask: football-data.org paid tiers, API-Football paid, Sportmonks, Opta/StatsBomb).
2. Same for odds (The Odds API paid plan or football-data.org Odds add-on). Until then value analytics stay
   paper-only and are not shown to end users.
3. Remove ESPN/FPL from any product path (undocumented endpoints).
4. LLM providers: current free tiers (Gemini, Groq) may use inputs for training and carry low limits; a paid
   tier with data-use terms is required before user-facing LLM forecasts.
Cost anchors (from the pricing pages above): football-data.org Standard EUR 49/month covers 30 competitions
at 60 calls/min; the Odds add-on is EUR 15/month.
