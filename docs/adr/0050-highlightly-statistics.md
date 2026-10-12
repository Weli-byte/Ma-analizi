# ADR 0050 — Highlightly for corners / cards / shots and the Super Lig history gap

Status: accepted · 2026-10-11 (owner provided a free-plan key)

## Verified (real calls from GitHub runners; the host is blocked from the owner's network)
Direct host `https://soccer.highlightly.net`, header `x-rapidapi-key`. League ids: EPL 33973, La Liga 119924, Bundesliga
67162, Serie A 115669, Ligue 1 52695, Süper Lig 173537 (seasons 2019-2026). `/matches?leagueId&season&limit=100&offset`
pages newest-first (380 matches = 4 calls). `/statistics/{matchId}`: corners, yellow cards, shots on target/off target for
4 of 4 sampled Premier League matches in each of 2022-2025 (xG and red cards only sometimes). BASIC plan: 100 requests
per day, `plan.message` "All data available with current plan".

## Decision
- `src/ingestion/highlightly.py` (client, key never in URL/logs/errors), `src/markets/stats_store.py` (resumable store,
  newest first, request budget, a missing statistic is None), `stats_overlay.py` (attaches statistics to the
  openfootball history and adds Highlightly matches where openfootball has none, i.e. the Süper Lig gap),
  `python -m src.markets.stats_collect` (a step of markets-cloud.yml, 15 requests per run, about 90/day).
- The count markets (corners, yellow cards, shots on target) are fitted from this store, but published artifacts include
  them only when `stats_plan: pro` (terms 4.1: the free plan is not subject to the same terms as paid plans; 6.1 allows
  storing and using data in products on paid plans). Until then they are research output.
- Data lives in the private data repo only. Unresolved Highlightly club names are reported in each artifact
  (`history.statistics.unresolved_names`), never auto-registered.

## To switch on (owner)
Buy PRO (USD 9.49/month, 7,500 requests/day) on highlightly.net, set `stats_plan: pro` and a larger
`stats_run_budget` in `configs/markets.yaml`; the backfill of 2024-25 onward then completes within days, not months.
Note: the free plan alone backfills at about 40-50 matches per day.

## Amendment 2026-10-11 — club names
`python -m src.markets.alias_tool [--apply]` maps Highlightly club names (from the store) to our clubs: only a clear
closest club of the same country is accepted, the rest is listed. The first run mapped 21 names automatically and 7 by hand
(Brighton, Leeds, Alavés, Brest, Gaziantep, Rizespor, Yeni Çorumspor = Çorum FK); 0 unresolved afterwards. With the
mapping, the overlay joined 250 store matches to the openfootball history and added 87 (Süper Lig 2026-27 results), so
the Süper Lig history now runs to 2026-10-11 (1223 matches). Re-run the tool after each new season is collected.

## Amendment 2026-10-11 — alias review with logos
`python -m src.markets.alias_review_page --open` writes a LOCAL page (artifacts/alias_review.html) listing every
`fuzzy_approved` alias under its club with the club logo, tick boxes and a copy button. The owner's network cannot reach
highlightly.net, so the one-off workflow `alias-logos.yml` downloads the logos on a runner into the private data repo
(branch `review-assets`) and the page embeds them. Logos are club trademarks, used for this review only (terms 6.3) and
never published.

## Amendment 2026-10-12 — logo search and the daily quota
`/teams?name=` (verified) finds the clubs the store has never seen. The free quota is 100 requests/day for everything
(collector included), so `alias-logos.yml` also runs daily at 00:20 UTC and skips clubs it already found (results are kept in
`_search.json` on the private `review-assets` branch). Remove the schedule once every club has its logo. Clubs the API does
not know under any tried spelling (Norwich City and Wolverhampton Wanderers at the time of writing) are listed, not guessed.
