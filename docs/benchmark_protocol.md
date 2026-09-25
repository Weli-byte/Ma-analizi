# Benchmark Protocol (v1)

Main output = 1X2 probability distribution `[p_home, p_draw, p_away]`. Not "match winner".

## 1. Splits
- Chronological / walk-forward only. **No random split. Ever.**
- Expanding or rolling window (config `evaluation.split_strategy`).
- Each split: train -> validation -> fit -> test prediction -> ledger -> metrics.
- Final test set is untouchable: no model selection, no hyperparameter tuning on it.
  Config enforces `final_test_touched: false`.
- All models compared on the identical fixture set.

## 2. Metrics (proper scoring rules)
Primary: Log Loss, multiclass Brier, RPS (ordered H<D<A), Calibration/ECE.
Secondary: Accuracy, exact-score accuracy, ROI, CLV.
Accuracy is never the sole success metric. Never declare one "overall winner" from one metric.
Report by: global, league, season, model family, prediction horizon — always with
confidence interval and sample size.

## 3. Leakage
See `docs/leakage_policy.md`. Nothing learned after `information_cutoff` may enter a feature.

## 4. Immutable predictions
- A prediction is never edited. A change = new record (new snapshot).
- Every record carries `model_id, model_version, feature_version, data_version,
  generated_at, information_cutoff`.
- `prediction_id` is deterministic from those keys (replay/idempotency).
- After kickoff no mutation. Locked predictions never change.

## 5. Ensembles
Only out-of-fold predictions train the meta-model. Final test predictions never enter meta training.

## 6. LLM benchmark
Historical (memorization risk, must be reported) and prospective/forward-only are separate
experiment types (`ExperimentType`). Provider calls mocked in tests.

## 7. Value engine
Paper trading only. Signals use only odds at the signal timestamp; closing odds only for CLV.
No real-money execution.

## 8. Reproducibility
Same config + same commit + same data_version => same splits, metadata, outputs.
Experiments record `config_hash`, `dataset_version`, `git_sha`, `seed`.
