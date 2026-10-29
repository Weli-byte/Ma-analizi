# ADR 0086 — Phase 2, Sprint 8: line-ups with an as-of contract (2026-10-28)

Plan: `Futbol_Tahmin_Optimizasyonu_Sprint_7_12_ULTRA_PRO_MAX.pdf`, sprint 8. Result: **CONDITIONAL** — the as-of line-up layer, the stage snapshots and the line-up features are built and tested on real stored line-ups; player impact, injuries and rest have no licensed data and are not built; no model reads a line-up feature. Nothing was bought.

## Source inventory (checked 2026-10-28)
| Need | Source | Status |
|---|---|---|
| Confirmed starting XI | ESPN public match summary (collected every ten minutes inside the shadow loop since 2026-10-25, ADR 0081) | real data, **UNOFFICIAL / research only** (no terms granting reuse); no announcement time |
| Injuries / suspensions | API-Football (suspended account, collection paused, ADR 0084; terms: no publication of derived data); FPL (EPL only, research only) | `NOT_VERIFIED` / no usable licensed feed; no feature |
| Per-player minutes, starts, xA (player impact) | none licensed (Highlightly's statistics are per team) | `BLOCKED`, not approximated |
| Player directory (transfers, loans, name variants) | none | provisional key only (team id + normalised name) |

## What was measured (real data, `docs/availability/sprint08_lineup_coverage.md`)
- 11 fixtures with a stored confirmed XI so far (19 files, SERIEA 6, SUPERLIG 3, LIGUE1 1, LALIGA 1). Some fixtures have two versions; the second sighting of one of them came nine minutes after kick-off and is kept but can never enter a pre-match snapshot.
- First pre-kick-off sighting: median **38 minutes** before kick-off (31 to 57). The collector looks from 100 minutes before, so the lower bound is not censored for these fixtures: ESPN shows the XI only about an hour before kick-off. **A `t-90m` snapshot is therefore UNKNOWN for every fixture with this source; `t-30m` is the first usable stage; `t-24h` is empty by construction.**
- Leakage self-check: 33 stage cutoffs, 0 snapshots with a later or post-kick-off sighting, 0 features changed when everything observed after the cutoff is removed; a negative-control test proves the check fires when the cutoff is ignored. Snapshot writing is idempotent (second run: 0 written, 33 unchanged, 0 conflicts).

## Contract
- `src/availability/lineup_asof.py`: one `LineupObservation` per stored file (all versions): fixture key, league, kick-off, clubs, `observed_at_utc`, `source_published_at_utc` (empty: none given), `lineup_status` (CONFIRMED = eleven distinct starters on both sides), `pre_kickoff`, formations, starter keys, goalkeepers, bench size, hashes. A file that is not eleven distinct starters per side, or is unreadable, is **quarantined and counted**, never repaired.
- Stage snapshots `t-24h` / `t-90m` / `t-30m`: the newest pre-kick-off sighting seen at or before the cutoff, else `UNKNOWN` (never PREDICTED, never filled). Written once to `artifacts/lineup_snapshots/<fixture>/<stage>.json`, only after the cutoff has passed; an existing file with other content is reported as a conflict and not overwritten.
- `src/availability/features.py`: `lineup_status`, formation, `formation_change`, `starters_changed_n`, `goalkeeper_change` (against the club's previous confirmed XI seen by the cutoff), `bench_size`, `usual_xi_missing_n` (the eleven most frequent starters of the last five confirmed XIs, needs three). NaN/None with a reason when an input is missing. Flag `LINEUP_FEATURES_ENABLED` (OFF): `fixture_features` returns `None` while off unless an experiment passes `force=True`.

## Decision gate (pre-registered here, before any ablation)
A baseline versus +line-up ablation (1X2, over/under, BTTS on the same matches and cutoffs; log loss, Brier, ECE, paired bootstrap) only with **at least 800 fixtures that have complete as-of features at `t-30m`** (confirmed XI and a usual XI for both clubs) and a league claim only with 100 such fixtures in that league. Today: 0 (no club has three earlier stored XIs yet). Gate not met, ablation not run, flag OFF. Known gap: the denominator (fixtures with no published XI) is not stored, so any later ablation must treat the stored fixtures as a selection of the matches that had an XI, and say so.

## Operational change
The line-up collector deleted folders older than three days (`KEEP_DAYS = 3`), which would have thrown away the very history this sprint needs; 6 of the 17 folders of 2026-10-25/26 were already gone when the problem was found. `KEEP_DAYS` is now 730; the site bundle ships only the line-ups of the last three days (`make_vercel_bundle.py`), the full history stays in the private data repository (about 6 KB per file).

## Not done, honestly
- No player impact, no injury or suspension feature, no rest/fatigue, no canonical player directory, no ablation, no model change, no migration, no new provider, no cost. Open owner decisions: a licensed line-up / injury / player-minutes feed (paid) and the API-Football account.

## Rollback
Flag OFF, no consumer: nothing to roll back at run time. Remove `src/availability/` and `tests/test_lineup_asof.py` to undo; restore `KEEP_DAYS = 3` only if the history is not wanted.
