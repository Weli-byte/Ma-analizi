# S2 Feature Engine (fv1)

Code: `src/features/` — `history.py` (cutoff-driven history), `compute.py`, `registry.py`,
`builder.py` (snapshots + parquet + lineage), `leakage_audit.py`.

## Rule
A match is visible only if `kickoff + 3h <= information_cutoff` (`RESULT_LAG`). Current fixture and
anything later are invisible by construction; `compute_features` rejects cutoff > kickoff.

## Features (per side `home_*` / `away_*`, plus `home_win_rate`, `away_win_rate`)
form_points_3/5/10, goals_for_avg_5, goals_against_avg_5, xg_avg_10, xga_avg_10, rest_days,
win_streak, loss_streak, opp_ppg_5 (opponent strength at same cutoff), home_win_rate (home team, last 10
home matches, >=5), away_win_rate (away team, last 10 away matches, >=5).
Insufficient history -> `None` (NaN) and `<name>_avail = 0.0`. Never 0. xG features are always NaN in dv1
(source has no xG); they fill automatically if `team_match_stats.xg` appears.

## Outputs
`python -m src.features.builder` -> `data/features/fv1/features.parquet` (one row per fixture,
cutoff = kickoff) + `feature_lineage.json` (registry hash, dataset version, per-feature contract).
Each `FeatureSnapshot` carries `available_at` per feature (latest source timestamp) and is validated.
`generated_at == information_cutoff` so rebuilds are byte-for-byte replayable.
Builder runs the leakage audit first and aborts on any violation.

## Leakage audit
`audit_leakage`: random fixture + random cutoff; scramble/delete every result not available at the cutoff
(incl. the fixture's own); features must be identical. Tests prove it catches 3 kinds of leaky code.

## Changing a feature
Edit registry -> `registry_hash` changes -> bump `FEATURE_VERSION`.
