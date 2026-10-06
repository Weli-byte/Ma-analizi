# Expected goals (xG) data source

**Status: NOT AVAILABLE. No validated xG source is integrated.** The `xg_avg_10` / `xga_avg_10`
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
