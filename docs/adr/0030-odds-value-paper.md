# ADR 0030 — S15 odds, edge/EV/CLV, paper ledger

Status: accepted · 2026-10-02 · extends ADR 0007 (odds timestamp policy)

## Decision
- **Source (real, verified 2026-10-02):** ESPN public scoreboard (`eng.1`, `esp.1`) exposes
  DraftKings 1X2 moneylines (open and current) for the current matchweek. Keyless, UNOFFICIAL,
  `RESEARCH_ONLY` (terms unverified). `src/odds/espn.py`; team names resolve through
  `TeamDirectory` (source `espn`; 3 aliases approved, owner to re-confirm).
- **`timestamp_quality` — owner to confirm this definition:** `exact` = the quote was read from a
  live bookmaker line by OUR collector and `observed_at` is our own fetch time (to the second).
  ESPN states no quote time, so the bookmaker-side time and the feed latency are UNKNOWN
  (`source_latency_s = None`, `source_latency_known = False` on every value row): an EV on such a
  quote assumes the line was live when read. Opening prices (no open time given) are `unknown`
  and the schema forbids calling them `exact`. Historical football-data.co.uk odds stay `unknown`
  and remain only the REFERENCE_MARKET_BASELINE.
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
