# ADR 0029 — S14 live match engine

Status: accepted · 2026-10-02

## Decision
- **Pipeline** (`src/live/`): feed -> normalized `LiveSnapshot` (`feeds.py`) -> events, de-duplicated
  by a content-derived `event_id` (`events.py`, `store.py`) -> immutable `MatchState` with a state
  hash (`state.py`) -> in-play model (`inplay.py`) -> immutable `LivePredictionRecord`
  (`schemas/live.py`, `engine.py`) -> calibration hook. Append-only store:
  `artifacts/live/<fixture>/{events,states,predictions}.jsonl` + frozen `prematch_rates.json`.
- **LIVE is a separate mode.** `LivePredictionRecord` is a different type from `PredictionRecord`:
  it requires `observed_at >= kickoff`; a pre-match record cannot be generated after kickoff; the
  pre-match ledger rejects a live record. The live ledger rejects a changed forecast for the same
  (fixture, model, version, state) and verifies identity on load.
- **Real feeds, verified 2026-10-02:** OpenLigaDB (keyless, German leagues): goals with minute,
  scorer, penalty / own-goal flags, running score, finished flag; no cards, substitutions, VAR or
  current minute. football-data.org free tier: status + score only (no goal list/cards/subs/VAR).
  The football-data.org IN-PLAY shape (`IN_PLAY`/`PAUSED`, running `fullTime`, optional `minute`)
  follows the v4 docs and has NOT yet been seen on a real in-play match (none was running); it is
  verified at the first real live match. Both feeds are `RESEARCH_ONLY` / terms unverified.
- **Nothing is assumed.** Event kinds a feed cannot supply are listed in `capabilities`; absence
  means unknown, never "no cards" (state has no card counts at all). A goal derived from a score
  change between polls is labelled `derived_from_score_change` and carries no minute/scorer. The
  minute is `reported`, else `last_event`, else `inferred_from_clock` (15-minute interval
  assumption); status without a feed flag is `inferred_from_clock`. Unmapped provider codes
  (postponed, suspended, ...) become `UNKNOWN`.
- **In-play model:** remaining goals ~ independent Poisson with rate `pre-match rate x remaining
  minutes / 90` (flat intensity); 1X2 from the final goal difference. Pre-match rates come from the
  Poisson fit (train+validation seasons) for leagues in the dataset (EPL, LaLiga); other leagues
  (Bundesliga) get events and state but no forecast (`missing: prematch_rates`), never a made-up
  prior. Unseen teams get league-average strength and this is written into the record's notes.
  The model ignores cards/subs/VAR/lineups/injuries. A forecast needs status IN_PLAY/HALF_TIME,
  score, minute and rates; otherwise the tick says what was missing. No forecast after full time.
- **Calibration:** no live outcome history exists, so records are `NOT_CALIBRATED`; the engine
  accepts a temperature (S9 `apply_temperature`) once live calibration data exists.
- **Scheduling:** `scripts/live_tick.ps1` (Windows task `FootballLiveTick`, every 2 minutes) makes
  one pass: one request per football-data.org league (10 req/min limit) + one OpenLigaDB request.
  Poll precision is therefore ~2 minutes. The PC must be on while matches run.

## Limitations (stated)
Flat scoring intensity (no late-goal surge, stoppage time unknown beyond a 2-minute tail); goal
timing from football-data.org is only known to the poll interval; OpenLigaDB entries are typed in
by community editors and may lag the real match. Not yet verified: a real in-play football-data.org
payload and a full live Bundesliga match through the scheduler (first chance: 2026-10-02 evening).
