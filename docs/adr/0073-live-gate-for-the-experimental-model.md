# ADR 0073 - Pre-registered live gate for the "Deneysel model (beta)" card

Date: 2026-10-24. Status: accepted. Builds on ADR 0071 and 0072.

## Context
ADR 0072 showed the experimental goal-market model on the owner's decision, on development evidence only (the frozen holdout is consumed). What was missing was a way for the model to pass or fail on matches that had not happened when it was built.

## Decision
* **The rule is fixed before any scoring** in `configs/experimental_gate.yaml` (version `exp-gate-v1`, registered 2026-10-24; the first artifacts with the block were written on 2026-10-23 and nothing had been scored). Its SHA-256 is pinned in `tests/test_experimental_gate.py`: editing the file after results are seen fails the test; a changed rule needs a new ADR and a new version.
* **What is scored** (`src/markets/track.experimental_scorecard`, run by the cloud job after the forecasts): for each finished match, the last artifact generated before kick-off that carries an `experimental` block. The experimental model and the current model are compared on the same matches: log loss of over/under 2.5 and of BTTS, paired block bootstrap (ISO weeks, 95%, 2000 samples, fixed seed).
* **Status**: `WITHDRAWN` when at least 100 matches are scored and, for one target, the whole interval of (experimental minus current) is above 0; `VERIFIED` when at least 150 matches are scored and, for both targets, the whole interval is below 0; otherwise `BETA`. Written to `artifacts/markets/experimental_status.json` and into `track_record.json` (`experimental`).
* **Effects**: the card shows "Beta: canlı doğrulama sürüyor (n/150 maç)" or "Canlı doğrulandı: n maçta ..." and links to the live record on the track page (section "Deneysel model: canlı kayıt", with the numbers, the interval and the rule). On `WITHDRAWN` the card is hidden at render time and `ExperimentalModel.fit` returns no model, so new artifacts carry no block. `MARKETS_EXPERIMENTAL=0` remains the manual switch.
* The status lags the forecasts by one cloud run (forecasts, then track, then the next run reads the status).

## Limits
* Two targets and two looks: the rule is deliberately conservative for "verified" (both targets, 150 matches) and quick to withdraw only when clearly worse. A verdict is about these two markets only; the result (1-X-2) is not touched.
* About 60 to 100 matches a week are expected; 150 matches therefore need roughly two to three weeks. That figure is an estimate, the live count is on the track page.
* Matches without a pre-kick-off artifact with the block are not counted (older artifacts, neutral-venue matches).
* This is a gate on the card, not an approval of any league model of the Sprint 5 registry.
