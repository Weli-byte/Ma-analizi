# ADR 0031 — Lineups from ESPN; stage information cutoff = run time

Status: accepted · 2026-10-02 · amends ADR 0027, extends ADR 0028

## Decision
- **Lineups** (`src/ingestion/lineups.py`): ESPN's public match summary, keyless, UNOFFICIAL,
  `RESEARCH_ONLY`. Verified on a REAL finished Premier League match (two sides, 11 starters + 9
  substitutes each, formation, jersey, position, formation place) and on a real upcoming match
  (rosters EMPTY until the lineup is announced). A lineup is `OBSERVED` only if BOTH sides have exactly
  11 starters, otherwise `UNKNOWN (not_announced_yet)`; a failed fetch is `FAILED`. Only
  starter/jersey/position/formation are read, never in-match fields. ESPN gives no announcement time,
  so only `observed_at` exists, and WHEN lineups appear before kickoff is not yet observed: the
  scheduled stage runs measure it (stages t-90m, t-30m, kickoff try; t-24h records
  `not_attempted_at_t-24h`).
- **Information cutoff = run time (amends ADR 0027).** A stage run now takes its snapshot time AFTER
  fetching injuries/lineups and uses that as `information_cutoff` (and `generated_at`); the nominal
  stage cutoff stays in the snapshot as `stage_cutoff`. Otherwise run-time observations (which are
  always later than the nominal cutoff) could never satisfy "nothing after the cutoff enters a
  feature". Features, injuries (`effective_at <= cutoff`) and lineups (`observed_at <= cutoff`, also
  re-checked when building the LLM snapshot) are all consistent with that single cutoff. The stage
  windows (ADR 0027) still bound how far the run time may be from the nominal time.
- **Use:** lineups enter the stage snapshot (and its hash) and the LLM prompt
  (`permitted_current_information.lineups`: formation, starters, substitutes). No statistical/ML model
  uses them (no history to train on).

## Limits
Football-data coverage only for eng.1 / esp.1; ESPN may fill rosters late or not at all; a lineup is a
provider display, not a confirmed team sheet. API-Football's free plan (owner account created
2026-10-02) is the next candidate source once a real response confirms what it returns for current
seasons.
