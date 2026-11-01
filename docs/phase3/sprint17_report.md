# Phase 3 sprint 17 — ablation laboratory and promotion gates (ADR 0096)

Every experiment below was registered (spec + SHA-256 in `docs/phase3/experiments/<id>/spec.json`) before its result was computed, and has at most one result. Deltas are candidate minus reference (negative = candidate better), with block-bootstrap intervals over ISO weeks and Bonferroni-corrected alpha. No experiment promoted anything automatically.

| Experiment | Track | Decision |
|---|---|---|
| S17-E01-champion-vs-naive-oof | RETROSPECTIVE_OOF | NOT_DEMONSTRATED_FOR_EVERY_MARKET |
| S17-E02-champion-vs-naive-live | PROSPECTIVE_LIVE | TOO_FEW_WEEKLY_BLOCKS |
| S17-E03-calibration | STORED_EVIDENCE | NO_GAIN |
| S17-E04-odds | STORED_EVIDENCE | NO_GAIN (production already uses the market at weight 1.0; the power-consensus rule waits for its gate) |
| S17-E05-league-models | STORED_EVIDENCE | NO_GAIN (no league model approved; the global model stays) |
| S17-E06-bivariate-poisson | RETROSPECTIVE_OOF | NO_GAIN |
| S17-E07-negative-binomial | RETROSPECTIVE_OOF | NO_GAIN |
| S17-E08-xg | NOT_RUN | NOT_RUN |
| S17-E09-lineups | NOT_RUN | NOT_RUN |
| S17-E10-llm | NOT_RUN | NOT_RUN |
| S17-E11-stacking | NOT_RUN | NOT_RUN |

## S17-E01-champion-vs-naive-oof

Hypothesis: The champion beats the naive baselines on the untouched 2023-24 season. Candidate: champion (model only). Baselines: prevalence of 2020-23 (1X2 shares, over rate, BTTS rate), always home, always over, always yes. Window: 2023-24 out-of-fold.

n = 1752; leakage gate PASS.
- 1x2: log loss 0.9764 vs 1.0771; delta -0.1008 [-0.1199, -0.0814], 39 weekly blocks, alpha 0.0167 
- btts: log loss 0.6807 vs 0.6871; delta -0.0064 [-0.0134, +0.0011], 39 weekly blocks, alpha 0.0167 
- over_2_5: log loss 0.6682 vs 0.6899; delta -0.0217 [-0.0309, -0.0126], 39 weekly blocks, alpha 0.0167 
- accuracy: always_home 43.1 %, always_over 54.9 %, always_yes 55.7 %, champion_1x2 53.0 %

## S17-E02-champion-vs-naive-live

Hypothesis: The published forecasts beat the naive baselines on the live record. Candidate: champion (published headline). Baselines: prevalence of 2020-23, always home, always over, always yes. Window: live record from 2026-10-09.

n = 259; leakage gate PASS.
- 1x2: log loss 0.8951 vs 1.0384; delta -0.1433 [-0.1958, -0.0813], 4 weekly blocks, alpha 0.0167 TOO_FEW_WEEKLY_BLOCKS
- btts: log loss 0.6912 vs 0.6838; delta +0.0074 [+0.0030, +0.0115], 4 weekly blocks, alpha 0.0167 TOO_FEW_WEEKLY_BLOCKS
- over_2_5: log loss 0.6592 vs 0.6824; delta -0.0233 [-0.0299, -0.0178], 4 weekly blocks, alpha 0.0167 TOO_FEW_WEEKLY_BLOCKS
- accuracy: always_home 50.2 %, always_over 63.3 %, always_yes 57.5 %, champion_1x2 62.9 %

## S17-E03-calibration

Hypothesis: A calibrator improves the champion. Candidate: temperature / Platt / isotonic / vector / Dirichlet / league-level. Baselines: identity champion. Window: fit 2020-22, select 2022-23, test 2023-24 (ADR 0089).

Stored evidence (docs/calibsel/sprint11_evaluation_manifest.json): {"decision": "NO_GAIN", "per_target": {"1x2": {"chosen": "temperature", "status": "KEEP_CHAMPION", "test_paired": {"alpha": 0.0125, "blocks": 39, "delta": 0.0017106762113452412, "interval": [-0.0015408306753174623, 0.004908065637216836]}}, "btts": {"chosen": "platt", "status": "KEEP_CHAMPION", "test_paired": {"alpha": 0.0125, "blocks": 39, "delta": -0.0019950221235036647, "interval": [-0.005830018009660768, 0.0016220899817139367]}}, "over_2_5": {"chosen": "platt", "status": "KEEP_CHAMPION", "test_paired": {"alpha": 0.0125, "blocks": 39, "delta": 0.0027303069767836696, "interval": [-0.0009176187671828829, 0.005990868670489088]}}}}

## S17-E04-odds

Hypothesis: Mixing the model into the de-vigged market improves the 1X2 headline. Candidate: convex blend / power-devig consensus. Baselines: market weight 1.0 (production). Window: 131 live rows (ADR 0087).

Stored evidence (ADR 0087, docs/oddsens/sprint09_market_vs_model.md): {"decision": "NO_GAIN (production already uses the market at weight 1.0; the power-consensus rule waits for its gate)", "gate": "300 report rows to change the market rule; 65 today", "model_only_minus_market_report_half": 0.0028, "power_consensus_minus_production_report_half": -0.0066, "selected_market_weight": 1.0}

## S17-E05-league-models

Hypothesis: League-specific or hierarchical models replace the global model. Candidate: league programme candidates. Baselines: global model. Window: development folds and the one-shot holdout (ADR 0063-0071).

Stored evidence (docs/league_models/s5_approval_decisions.json (ADR 0063-0071)): {"decision": "NO_GAIN (no league model approved; the global model stays)", "status_counts": {"APPROVED_FOR_CANARY": 0, "BLOCKED": 5, "GLOBAL_KEPT": 16, "REJECTED": 2, "SHADOW_INCONCLUSIVE": 4}}

## S17-E06-bivariate-poisson

Hypothesis: A bivariate Poisson score grid beats the champion's Dixon-Coles grid. Candidate: bivariate Poisson (shape fitted on 2020-23). Baselines: champion Dixon-Coles grid. Window: 2023-24 out-of-fold.

n = 1752; leakage gate PASS.
- 1x2: log loss 0.9775 vs 0.9764; delta +0.0011 [+0.0001, +0.0022], 39 weekly blocks, alpha 0.0083 NO_GAIN
- btts: log loss 0.6813 vs 0.6807; delta +0.0006 [+0.0001, +0.0010], 39 weekly blocks, alpha 0.0083 NO_GAIN
- over_2_5: log loss 0.6683 vs 0.6682; delta +0.0002 [-0.0001, +0.0004], 39 weekly blocks, alpha 0.0083 NO_GAIN
- shape fitted on 2020-23: 0.02 (reproduced on a second fit: True)

## S17-E07-negative-binomial

Hypothesis: Negative binomial margins beat the champion's Dixon-Coles grid. Candidate: negative binomial (dispersion fitted on 2020-23). Baselines: champion Dixon-Coles grid. Window: 2023-24 out-of-fold.

n = 1752; leakage gate PASS.
- 1x2: log loss 0.9780 vs 0.9764; delta +0.0017 [+0.0003, +0.0031], 39 weekly blocks, alpha 0.0083 NO_GAIN
- btts: log loss 0.6819 vs 0.6807; delta +0.0012 [+0.0003, +0.0020], 39 weekly blocks, alpha 0.0083 NO_GAIN
- over_2_5: log loss 0.6683 vs 0.6682; delta +0.0001 [-0.0001, +0.0002], 39 weekly blocks, alpha 0.0083 NO_GAIN
- shape fitted on 2020-23: 160.0 (reproduced on a second fit: True)

## S17-E08-xg

Hypothesis: xG features improve the champion. Candidate: +xG (ADR 0085). Baselines: champion. Window: n/a.

NOT_RUN — data gate not met: 49 of 1,500 matches with complete as-of xG when ADR 0085 was written; the free plan is research only.

## S17-E09-lineups

Hypothesis: Line-up features improve the champion. Candidate: +line-ups (ADR 0086). Baselines: champion. Window: n/a.

NOT_RUN — data gate not met: 0 of 800 fixtures with complete as-of line-up features when ADR 0086 was written.

## S17-E10-llm

Hypothesis: An LLM forecast or commentary improves the champion. Candidate: LLM providers (ADR 0024 / 0026). Baselines: champion. Window: n/a.

NOT_RUN — NOT_CONFIGURED: real calls cost money and the owner's budget is 0; mock or replayed answers never enter the comparison.

## S17-E11-stacking

Hypothesis: A trained stacker over market and model improves the headline. Candidate: OOF logistic stacker (ADR 0087). Baselines: market weight 1.0. Window: n/a.

NOT_RUN — data gate not met: 131 of 600 rows with three or more bookmakers when ADR 0087 was written; odds collection is paused (quota).
