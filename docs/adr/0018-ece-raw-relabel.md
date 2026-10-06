# ADR 0018: Relabel the `ece` report field to `ece_raw`

## Status
Accepted — 2026-09-30 (S0-S7 hardening pass, Phase 26, audit finding L-05).

## Context
`src/evaluation/metrics.py`'s `METRICS` dict and every report that iterates it (`run_baselines.py`,
`configs/evaluation.yaml`'s `metrics:` list, test fixtures) label Expected Calibration Error
simply `ece`. No model calibrates its output today (S9 — calibration — is future scope; S0-S7
hardening Phase 25 / ADR-adjacent finding M-13 added the `raw_probs_` contract specifically in
anticipation of it). The VALUE reported is correct — ECE computed against each model's raw,
uncalibrated output — but the LABEL `ece` reads as if it already reflects calibrated quality.

## Decision
Rename the metric's dict key (and every report/column that names it) from `ece` to `ece_raw`,
everywhere the string is used as a field name:
- `src/evaluation/metrics.py::METRICS` dict key.
- `src/config/__init__.py`'s `EvaluationConfig.metrics` `Literal[...]` type.
- `configs/evaluation.yaml`'s `metrics:` list.
- `src/evaluation/run_baselines.py`'s Markdown report columns.
- `tests/fixtures/golden/expected/golden.json` (regenerated via the `regenerate-golden` CI
  workflow, never locally — see ADR-0017's amendment / H-09).

The `ece()` FUNCTION name in `metrics.py` is unchanged — only the report-facing key/label moves.
When S9 lands and calibration is implemented, `ece_calibrated` (or equivalent) can be added
alongside `ece_raw` without another rename, since the "raw" qualifier is already in place.

## Consequences
- Any external consumer of `report.json`/`golden.json` that reads the `ece` key must switch to
  `ece_raw`. Within this repo, all call sites are the ones listed above; this ADR's accompanying
  commit updates every one of them in the same change plus regenerates the golden fixture.
- No metric VALUE changes. This is a pure relabeling; the golden fixture's `ece_raw` value for
  every model is bit-identical to its previous `ece` value — only the key differs.
- `docs/baselines.md`/`docs/walk_forward.md`/`docs/gbm.md` example report snippets, if any name
  `ece` literally, are updated for consistency (see the accompanying commit's diff).

## Alternatives considered
- **Leave `ece` unrenamed, document the raw status in prose only** — rejected: this is exactly
  the ambiguity the audit flagged (a reader has to know from prose, not the field itself, that
  it is uncalibrated); a self-describing key costs nothing once no external consumer depends on
  the old name yet (no S8+ sprint has shipped against this report format).
- **Wait until S9 actually implements calibration, then rename** — rejected: renaming later would
  touch the SAME call sites plus whatever S9 code by then depends on the `ece` key, and would
  require a second golden regeneration; doing it now, while the field is still young, is cheaper.
