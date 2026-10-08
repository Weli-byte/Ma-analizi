# ADR 0030 — S15 odds, edge/EV/CLV, paper ledger

Status: accepted · 2026-10-02 · extends ADR 0007 (odds timestamp policy)

## Decision
- **Source (real, verified 2026-10-02):** ESPN public scoreboard (`eng.1`, `esp.1`) exposes
  DraftKings 1X2 moneylines (open and current) for the current matchweek. Keyless, UNOFFICIAL,
  `RESEARCH_ONLY` (terms unverified). `src/odds/espn.py`; team names resolve through
  `TeamDirectory` (source `espn`; 3 aliases approved, owner to re-confirm).
- **`timestamp_quality` (strict; the owner REJECTED the first, looser definition on 2026-10-02):**
  `exact` = the SOURCE supplies its own capture/update time for the quote (`provider_timestamp`)
  and we record when we received it, so the feed latency is MEASURED
  (`source_latency_s = observed_at - provider_timestamp`, never guessed; a provider clock more than
  5 s ahead of ours is not trusted as exact). `approximate` = only our own observation time is known.
  `unknown` = no usable time (opening prices, historical football-data odds). ESPN's payload carries
  no quote time (searched 2026-10-02), so its DraftKings quotes are `approximate`: kept as a time
  series and shown only as a labelled market REFERENCE (de-vigged probabilities), never as edge,
  EV, CLV or a paper bet. The model enforces this (`exact` without a provider timestamp / measured
  non-negative latency is a validation error).
- **Route to exact odds:** The Odds API (`src/odds/theoddsapi.py`) documents a per-market
  `last_update`. It needs a free key (`THE_ODDS_API_KEY`, owner action: email signup), and the adapter
  is UNVERIFIED (`verified_on=None`) until `pytest -m live tests/integration/test_theoddsapi_live.py`
  parses a real response; until then `collect` prints NOT_CONFIGURED and edge/EV/CLV stay unavailable.
  Its terms for the free plan are unverified.
- **Gate** (`value.py`): edge/EV/CLV are produced only from a complete 1X2 snapshot (same
  bookmaker, same observation time) of `exact` quotes, observed at/after the forecast was
  generated and strictly before kickoff. Otherwise the row is `NOT_ELIGIBLE` with the reason and
  NO numbers (no EV is exposed for a non-exact quote).
- **Math** (`math.py`): implied = 1/odds; de-vig = proportional normalisation; edge = model p -
  de-vigged market p; EV = p x odds - 1 at the OFFERED price; CLV = taken odds / fair closing price
  - 1 where the closing reference is the LAST exact snapshot the collector saw before kickoff
  (up to one collection interval older than the true close; absent -> CLV is None, not invented).
- **Paper only** (`paper.py`, `configs/odds.yaml`): the best-EV selection of an ELIGIBLE row becomes
  an immutable `PaperBet` (abstract units) when edge >= `min_edge` and EV >= `min_ev`; one bet per
  (fixture, model, selection), the first decision stands; settlement is a separate record. Reports
  state the sample size and say plainly that fewer than 30 settled bets is noise.
- **Pipeline:** `python -m src.odds.run collect | value [--paper] | settle`; Windows task
  `FootballOddsTick` every 15 minutes (`scripts/odds_tick.ps1`). Forecasts come from the locked S13
  stage predictions; until a stage has run (first window 2026-10-08) `value` reports "no locked
  pre-match forecast yet".

## Limits
One bookmaker (DraftKings via ESPN), current matchweek only, no history; a 15-minute cadence makes
the "closing" reference up to 15 minutes stale; no staking strategy (flat 1 unit); no claim of
profitability — a handful of paper bets proves nothing.

## Amendment 2026-10-08 — exact odds are verified and collected in the cloud
- **Verified on a real response** (GitHub Actions, `scripts/probe_theoddsapi.py`): The Odds API v4 `h2h`
  carries a per-market `last_update`; sport keys `soccer_epl`, `soccer_spain_la_liga`; the free plan
  reported 500 credits and one league call cost 2. `src/odds/theoddsapi.py` is now `SUPPORTED`
  (`verified_on` 2026-10-08); `tests/test_odds_exact.py` parses the REAL captured event (Arsenal v Leeds,
  three bookmaker keys, measured latency ~tens of seconds) and drives the REAL forecasts of 2026-10-02
  to ELIGIBLE value rows with no injected timestamps. Bookmaker identity is `title (key)` because two
  feeds share the title "Betfair".
- **ISP block:** from the owner's network the API host fails TLS (`WRONG_VERSION_NUMBER`), the same
  interference that served a "Güvenli İnternet" page for the site. Not bypassed. Collection therefore
  runs from GitHub (`.github/workflows/odds-exact.yml`, every 10 min), spends credits only while a window
  is open (`src/odds/schedule.py`: t-24h, t-95..65, t-35..10 and the last 12 minutes before kickoff;
  one call per league serves all its fixtures), keeps `odds_api_reserve_credits` (25) unspent, and commits
  quotes to the `odds-data` branch. The PC pulls them (`python -m src.odds.run sync-remote` ->
  `artifacts/odds_remote/`, read-only) and `value`/`settle` read both stores.
- Budget estimate: about 4 windows x 1 call x 2 credits per league per match day; a normal week stays
  well under the 500-credit month. Terms of the free plan (redistribution) are NOT verified: only a
  minimal real sample is committed as a test fixture.
