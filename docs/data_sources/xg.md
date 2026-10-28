# Expected goals (xG) data source

**Status (updated 2026-10-27, ADR 0085): a real xG source exists but is RESEARCH ONLY and too young to use.** Highlightly returns `expected_goals` for most matches (335 of 336 fetched 2026-27 matches); the free plan is research-only, product use needs PRO; no publication time, so history starts at our fetch time (2026-10-13). `src/xg/` builds as-of safe features behind the OFF flag `XG_FEATURES_ENABLED`; no model reads them. The text below is the original note: no validated source was integrated when it was written.

**Original status: NOT AVAILABLE. No validated xG source is integrated.** The `xg_avg_10` / `xga_avg_10`
features are declared as `experimental` in `src/features/registry.py` (`EXPERIMENTAL`), are never produced,
never written to `features.parquet`, and never used for training. xG is never imputed or faked.

| Field | Value |
|---|---|
| source | none selected (a future dedicated task chooses one) |
| license | n/a — must be reviewed before selection (see licensing.md; many xG providers forbid redistribution and model training) |
| coverage | n/a |
| timestamp semantics | required: xG must carry a publication time (`available_at`) so the leakage rule "only matches with result/stat availability ≤ information_cutoff" can be applied; post-match xG of the current fixture is forbidden |
| availability | none |
| historical depth | target ≥ 5 seasons for EPL and La Liga |
| limitations | football-data.co.uk CSVs contain shots and corners but no xG |

Candidate sources to evaluate (unreviewed): Understat, FBref/StatsBomb, API-Football, Opta. Selection
criteria: commercial-use license, per-match timestamps, coverage of the S12 league list, cost.

When a source is added: extend the schema (`team_match_stats.xg`), bump `feature_version`, promote the two
specs from `experimental` to `active`, add leakage tests for post-match xG, and write an ADR.
