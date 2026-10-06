# ADR 0022: S11 OOF ensemble — split protocol, model registry linking, LLM participation

## Status
Accepted — 2026-10-01.

## Context
S11 asks for: OOF predictions for every base model, a meta-model comparing simple average,
validation-weighted average, logistic regression stacking, and LightGBM stacking, final-test
rows never entering meta training, ensemble calibration as a separate pipeline, season/league/
horizon improvement reporting, and the model registry linking base versions to the meta version.

## Decision: reuse walk-forward's OOF predictions, never recompute them

`run_ensemble.py` reads `artifacts/walk_forward/<tag>/predictions.jsonl` (S7) rather than
re-fitting or re-predicting anything. Every row in that file is already genuinely
out-of-fold by construction (each fold's model is fit only on strictly prior seasons,
S7/ADR-0016) and already excludes final-test seasons structurally
(`walk_forward.py`'s own `EvaluationContext(EvalMode.VALIDATION, ...)` blocks them at the
source) — "final test predictions never enter meta training" is therefore true by inheritance,
not a separate check this module needs to re-implement.

## Decision: three-way chronological split (fit-ensemble / fit-calibration / report)

The common OOF fixture set (every base model predicted it) is split into three contiguous
chronological thirds:

1. **fit-ensemble** (first third): fits the combiner — `validation_weighted`'s per-model
   weights, `logistic_stacking`'s `LogisticRegression`, `lightgbm_stacking`'s booster.
   `simple_mean` has nothing to fit here, but is evaluated through the identical pipeline for a
   fair comparison.
2. **fit-calibration** (second third): the already-combined ensemble's own output on this third
   fits one temperature `T` (`calibration.fit_temperature`, S9's exact mechanism, applied one
   level up — to the ensemble's output, not a base model's).
3. **report** (final third): BOTH raw and calibrated (`T`-scaled) ensemble metrics are computed
   here, and ONLY here — never on rows that trained the combiner or fit `T`.

This extends ADR-0020's two-way split one level: S9 established "fit calibration disjoint from
report," S11 adds "fit combination disjoint from fit calibration disjoint from report." A model
needs `>= 3 * MIN_SPLIT_ROWS` (45) common OOF rows or the whole variant is skipped with a stated
reason, never silently fit on too little data.

## Decision: meta input features

Each base model's `[p_home, p_draw, p_away]`, concatenated in a FIXED order
(`build_meta_features`). Confidence is supported (optional extra column per model) for
LLM-sourced predictions, which have one (`LLMCallRecord.confidence`); classical models don't
report a confidence distinct from their probability vector, so they contribute only their three
probabilities. League/season are NOT fed into the meta-model as FEATURES (that would let the
stacker learn league-specific biases that may not generalize to leagues/seasons it never trained
on) — they are used only for GROUPING the report-split's metrics after the fact
(`_grouped_metrics`), matching how `leaderboard.py` already separates "what the model learns
from" and "how results get sliced for reporting."

## Decision: LLM base models participate IF their predictions exist, never required

The sprint names GPT/Claude/Gemini among the base models. `_common_fixtures`/`_probs_matrix`
operate on `PredictionRecord`s generically by `model_id`, with no classical-model assumption —
an `llm_openai_gpt_4o`-style `model_id` (S8) would join the ensemble exactly like `elo` or
`xgboost` IF its predictions were loaded. This commit does NOT wire `artifacts/llm_runs/*/
predictions.jsonl` into `run_ensemble.py`'s loader, because: (a) no LLM predictions exist in a
default checkout (`configs/provider.yaml` disabled by default — S8 ADR-0019), so there's nothing
to test honestly against, and (b) `model_cfg.walk_forward_models`/`models` names the base set
today and has no LLM-aware entries, so adding this now would be dead, untested code. The data
model places no obstacle in the way: whoever wires it up later only needs to merge the LLM
ledger's records into the same `predictions` list before `_common_fixtures` runs.

## Decision: "horizon" stays out, same as S9

No live/forward fixture feed exists yet (S13/S14). Noted here rather than re-documented per ADR
— see ADR-0020's identical decision for `leaderboard.py`.

## Decision: model registry linking

Every `EnsembleVariantResult` records `meta_version` (`ENSEMBLE_VERSION`, bumped only when the
COMBINATION LOGIC changes, mirroring how `model_version` works for every other model class) and
`base_model_ids` (the exact list of base `model_id`s that went into it, sorted). This is the
"base versions + meta version" link the sprint asks for — written into `report.json`, not a
separate registry database (no such registry exists yet in this repo; `ExperimentRecord` is the
closest analogue and could gain an ensemble-specific variant later if S16 MLOps wants one).

## Alternatives considered
- **k-fold nested CV for the meta-model** — more statistically efficient than a single
  chronological three-way split, same reasoning as ADR-0020's "alternatives considered": the
  complexity isn't justified yet; revisit if ensemble quality itself becomes a research question.
- **Feed league/season into the meta-model as features** — rejected per the decision above
  (overfitting risk to leagues/seasons seen during combiner training).
- **A full LLM-aware ensemble wiring now** — rejected: no real LLM predictions exist to test it
  honestly against without spending the owner's API budget; deferred until S8's CLI has actually
  been run with real keys at least once.
