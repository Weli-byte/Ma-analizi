# Feature engine (S2, fv2)

Code: `src/features/` — `history.py` (cutoff-driven history), `compute.py`, `registry.py`, `builder.py`
(snapshots + parquet + lineage), `artifact.py` (verified loading), `availability.py`, `leakage_audit.py`.

## Rule
A match is visible only if it is FINISHED and `result_available_at_utc <= information_cutoff`
(`result_available_at_utc` is inferred = kickoff + `features.result_lag_hours`, ADR 0006). The current fixture,
later matches, and postponed/cancelled/abandoned/scheduled matches are invisible by construction.
`compute_features` rejects cutoff > kickoff.

## Features (per side `home_*` / `away_*`, plus side-specific `home_win_rate` and `away_win_rate`)
form_points_3/5/10, goals_for_avg_5, goals_against_avg_5, rest_days_raw, rest_days_capped (cap =
`features.rest_days_cap`), season_break_flag, win_streak, loss_streak, opp_ppg_5 (opponent strength at the same
cutoff), home_win_rate (home team's last 10 home matches, ≥5), away_win_rate (away team's last 10 away matches, ≥5).
xG features are EXPERIMENTAL and not produced (docs/data_sources/xg.md).

## Missing values (ADR 0009)
`None`/NaN + `<name>_available` flag + reason (`dataset_start | new_team | insufficient_history |
source_missing`). Never zero. Model guidance: XGBoost/LightGBM take NaN natively (also pass the flags);
scikit-learn models need an imputer fitted on TRAIN only plus the flags.

## Artifacts (`data/features/<data_version>/<feature_version>/`)
`features.parquet` (one row per fixture, cutoff = kickoff − offset, JSON column of reasons) and
`feature_lineage.json` (data_version, dataset content hash, feature_version, builder_version, registry hash,
source files + checksums, git SHA/dirty, config hash, python/lock hash, parquet sha256, content hash, audit info).
`load_features` verifies all of it and raises `StaleArtifactError` on any mismatch.

## Leakage audit (`audit_leakage`)
Random fixture + cutoff; scrambles/deletes every result, post-match stat and closing odd not available at the
cutoff (and the fixture's own); features must not change. The builder aborts on any violation. Tests prove the
audit catches nine intentionally broken implementations (current result, future goals, future fixtures, future
rolling window, future opponent strength, future standings, post-match statistics, unavailable closing odds,
results published after the cutoff) plus a cutoff-ignoring one.

## Changing a feature
Edit the registry → `registry_hash` changes → bump `FEATURE_VERSION` (and configs) → rebuild artifacts.
