# ADR 0009: Missing features and fallbacks

## Status
Accepted — 2026-09-26.

## Context
`recent_form_naive` silently fell back to the historical prior when features were missing (15/760 rows);
an absent or stale feature file would have degraded results without any signal.

## Decision
- Every unavailable feature is `NaN/None` + `<name>_available = 0` + a REASON code:
  `dataset_start` (no earlier season exists in the dataset), `new_team` (team absent although earlier
  seasons exist: promoted/new), `insufficient_history` (some matches, fewer than the window),
  `source_missing` (source lacks the field). A missing feature RECORD or an undeclared reason is
  "unexpected" (possible bug).
- `FeatureAvailabilityReport`: total_fixtures, available/missing/fallback/unexpected rows, fallback_rate,
  missing_by_feature/season/team, reasons. Models declare `required_features`; the runner reports and
  enforces per mode.
- STRICT/FINAL: zero unexpected rows and fallback_rate ≤ threshold. RESEARCH: unexpected rows count toward
  the threshold. DEVELOPMENT: only the threshold. Thresholds: `evaluation.yaml: max_fallback_rate`.
- Never impute without an availability indicator. Model guidance: XGBoost/LightGBM take NaN natively
  (feed `_available` flags too); scikit-learn models need an explicit imputer fitted on TRAIN only, plus the
  flags.
- xG features are experimental and NOT produced (docs/data_sources/xg.md).

## Alternatives
Silent imputation (rejected); dropping rows (rejected: biases the eval set).

## Consequences
Real validation run: 25/1520 rows fall back (1.6%), all declared (`new_team` 5, `insufficient_history` 20).
