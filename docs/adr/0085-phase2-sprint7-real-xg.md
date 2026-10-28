# ADR 0085 — Phase 2, Sprint 7: real xG/xGA with an as-of contract (2026-10-27)

Plan: `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf` (27 Oct 2026), sprint 7. Result: **CONDITIONAL** — the infrastructure is complete and tested, the data is real but too young and licensed for research only, so no model reads an xG feature.

## Source inventory (checked 2026-10-27)
| Source | Real xG? | Evidence | Status |
|---|---|---|---|
| Highlightly `/statistics/{id}` (ADR 0050) | **yes**, `expected_goals` per team and match | 335 of 336 fetched 2026-27 matches in the stored rows carry both values (live fetches from 2026-10-13 to 2026-10-27 by the cloud collector; the PC network cannot reach the host) | **research only** on the free BASIC plan; product use needs PRO (terms 4.1 / 6.1, ADR 0050) → `LICENSE_REVIEW_REQUIRED` for a product, no PRO plan |
| API-Football | claimed in its fixture statistics, **not verified** | the account is suspended (checked today: `Your account is suspended`); its terms forbid publication of derived data (licensing.md) | `NOT_VERIFIED` |
| Understat, FBref / StatsBomb, Opta | not reviewed | no key, terms not read, no licence document | `LICENSE_REVIEW_REQUIRED`, not integrated |
| football-data.org, openfootball, ESPN scoreboard | **no** xG | goals only (shots are not xG and are never labelled xG) | n/a |

## As-of contract
- `src/xg/observations.py`: one `Observation` per finished Highlightly match (unique by source match id), fields `source_id, source_match_id, fixture_key, competition_id, season, kickoff_utc, home_id, away_id, goals, xg_home, xg_away, published_at_utc, ingested_at_utc, available_at_utc, coverage_status, source_version, checksum`. Club ids come from the existing `TeamDirectory` (never auto-registered; unresolved names are listed in the report, their matches left out).
- The provider gives **no publication time**, so `published_at_utc` is empty and an xG value is usable as history only from `available_at_utc` = the moment we fetched it. Matches fetched weeks after the final whistle are therefore invisible to any earlier prediction cutoff. **A backtest on past dates is not possible with this data; the history grows prospectively.** (229 of the 335 observed matches were fetched more than two days after kick-off.)
- A match without both values is `MISSING_XG` / `NO_STATISTICS`, never filled; an invalid value (negative, non-finite) counts as missing. No zero imputation.
- `src/xg/features.py`: rolling 3/5/10, exponentially weighted (half-life 5 matches), home/away split, opponent-adjusted (xG scored minus the opponent's mean xG conceded, league mean as fallback; both as of the cutoff), `n_obs`, `last_available_at`, `coverage_pct` (observed share of the last 10 finished matches), `xg_available` + reason. Fewer than 3 observations → NaN, not a number. The target fixture is excluded by key and every row fetched after the cutoff is invisible.
- Flag: `XG_FEATURES_ENABLED` (OFF). `fixture_features` returns `None` while it is off unless an experiment passes `force=True`. No model, feature file or API reads it (the old `xg_avg_10` specs in `src/features/registry.py` stay `experimental`).

## Gate (pre-registered here, before any ablation)
A baseline versus +xG ablation (1X2, over/under, BTTS; log loss, Brier, ECE, paired bootstrap, same matches, same cutoffs) is run only with **at least 1,500 matches that have complete as-of xG features for both clubs**, and a league-level claim only with 150 such matches in that league. Today: 49 such matches. Gate **not met**, ablation not run, the flag stays OFF. The free plan's collector adds a few dozen matches a day, so the gate is a matter of weeks to months (an estimate, not a measurement), and the licence (PRO plan) must be settled before any product use.

## Evidence
- Coverage by league × season and leakage self-check: `docs/xg/sprint07_xg_coverage.md` (`python -m src.xg.report`; 200 matches checked, 0 own-row and 0 future-row effects; a negative-control test proves the check fires when the availability rule is removed).
- Baseline re-measured: `docs/xg/sprint07_baseline.md` (`python -m src.xg.baseline`). The first 138 matches by date reproduce the plan's figures exactly (1X2 94/138, O/U 84/138, BTTS 76/138, score 10/138, always-over 95/138). On all 194 matches the model is behind "always over" (62.9 % vs 64.9 %) and "always yes" (57.7 % vs 60.3 %) in accuracy; the intervals overlap.
- Tests: `tests/test_xg.py` (real EPL 2026-27 rows from the store as fixture, 59 with xG; time-rule tests on labelled synthetic rows).
- Cost: nothing spent; Highlightly BASIC is free (100 requests a day, shared with the statistics collector); PRO would be USD 9.49 a month.

## Not done (blocked, honestly)
- No ablation (data too young), no model change, no migration (nothing is written into the existing stores; `artifacts/xg/` can be exported on demand with `--observations-out`), no commercial use.
- Open owner decisions: Highlightly PRO for product use; whether to look for a source with a publication time and history (needs a paid licence).

## Rollback
Nothing to roll back at run time (flag OFF, no consumer). Remove `src/xg/` and `tests/test_xg.py` to undo the sprint.
