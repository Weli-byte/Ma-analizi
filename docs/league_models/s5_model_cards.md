# Sprint 5 model cards and decisions (FROZEN HOLDOUT, evaluated once)

Calibrated probabilities (method chosen by inner walk-forward, fitted on out-of-fold development predictions). Odds-aware models: NOT_AVAILABLE. A status is a product decision under the declared policy, not a guarantee.

| Cell | Candidate | Gate status | Matrix label | n | Reasons |
|---|---|---|---|---|---|
| `ENG1|1x2` | E_elo_bridge | SHADOW_INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 380 | the log-loss interval against at least one reference includes 0 |
| `ENG1|btts` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 380 | no candidate is below every reference: global model kept |
| `ENG1|over_under` | B_gbm_league_totals | REJECTED | LOST | 380 | significantly worse than dixon_coles_totals, A_dc_ridge_tuned_totals |
| `ESP1|1x2` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 380 | no candidate is below every reference: global model kept |
| `ESP1|btts` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 380 | no candidate is below every reference: global model kept |
| `ESP1|over_under` | B_dc_league_totals | SHADOW_INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 380 | the log-loss interval against at least one reference includes 0; Brier is not better against every reference |
| `FRA1|1x2` | E_elo_bridge | SHADOW_INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 305 | the log-loss interval against at least one reference includes 0; Brier is not better against every reference |
| `FRA1|btts` | B_gbm_league_goals | REJECTED | LOST | 305 | significantly worse than A_dc_ridge_tuned_goals; calibration (ECE) materially worse than A_gbm_tuned_goals |
| `FRA1|over_under` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 305 | no candidate is below every reference: global model kept |
| `GER1|1x2` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 306 | no candidate is below every reference: global model kept |
| `GER1|btts` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 306 | no candidate is below every reference: global model kept |
| `GER1|over_under` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 306 | no candidate is below every reference: global model kept |
| `ITA1|1x2` | B_gbm_league | SHADOW_INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 380 | the log-loss interval against at least one reference includes 0; Brier is not better against every reference |
| `ITA1|btts` | - | GLOBAL_KEPT | LOST | 380 | no candidate is below every reference: global model kept |
| `ITA1|over_under` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 380 | no candidate is below every reference: global model kept |
| `TUR1|1x2` | - | GLOBAL_KEPT | NO_DATA | 338 | 0 dev rows < 150 |
| `TUR1|btts` | - | GLOBAL_KEPT | NO_DATA | 338 | 0 dev rows < 150 |
| `TUR1|over_under` | - | GLOBAL_KEPT | NO_DATA | 338 | 0 dev rows < 150 |
| `UEFA_UCL|1x2` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 189 | no candidate is below every reference: global model kept |
| `UEFA_UCL|btts` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 189 | no candidate is below every reference: global model kept |
| `UEFA_UCL|over_under` | - | GLOBAL_KEPT | INSUFFICIENT_EVIDENCE | 189 | no candidate is below every reference: global model kept |
| `UEFA_UECL|1x2` | - | GLOBAL_KEPT | NO_DATA | 0 | no candidate is below every reference: global model kept |
| `UEFA_UECL|btts` | B_gbm_league_goals | BLOCKED | NO_DATA | 0 | no holdout rows for the cell |
| `UEFA_UECL|over_under` | B_gbm_league_totals | BLOCKED | NO_DATA | 0 | no holdout rows for the cell |
| `UEFA_UEL|1x2` | B_dc_league | BLOCKED | NO_DATA | 0 | no holdout rows for the cell |
| `UEFA_UEL|btts` | B_gbm_league_goals | BLOCKED | NO_DATA | 0 | no holdout rows for the cell |
| `UEFA_UEL|over_under` | C_gbm_local_totals | BLOCKED | NO_DATA | 0 | no holdout rows for the cell |
