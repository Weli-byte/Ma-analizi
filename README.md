# Global Football Forecasting Benchmark & Intelligence Platform

> Predict the probability. Benchmark the models. Measure the edge.

Output = 1X2 probability distribution, judged by Log Loss / Brier / RPS / calibration. Plan:
`docs/reference/`. Working rules: `CLAUDE.md`. Decisions: `docs/adr/`.

## Supported environments
Python **3.12** and **3.14** (CI matrix). Linux (CI) and Windows 11. Dependencies: `pyproject.toml` is the single
source; `requirements.lock` is a hash-pinned universal lock (ADR 0011).

## Install
```bash
python -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.lock
pip install --no-deps -e .
ruff check . && pytest                   # 300+ tests, golden + reproducibility included
```
Regenerate the lock after changing dependencies:
`uv pip compile pyproject.toml --extra dev --universal --generate-hashes --python-version 3.12 -o requirements.lock`

## Run the research pipeline
```bash
python -m src.data.download                      # or place files manually, see docs/data_pipeline.md
python -m src.data.checksums verify
python -m src.data.pipeline --mode strict        # atomic; writes data/processed/<data_version>/
python -m src.features.builder --mode strict     # data/features/<data_version>/fv2/
python -m src.evaluation.run_baselines --mode strict   # artifacts/runs/<run>/report.md
```
Modes: `development | research | strict` (`final` only via `python -m src.evaluation.final`).

## Layout
`apps/` (api, dashboard, worker; later sprints) · `src/` (schemas, config, data, features, models,
evaluation, ...) · `configs/` · `data/{raw,provenance,processed,features}` · `docs/` · `tests/`
(+ `tests/fixtures/golden`) · `reports/remediation/` · `scripts/`

## Docs
`docs/benchmark_protocol.md` · `docs/leakage_policy.md` · `docs/versioning.md` · `docs/data_pipeline.md` ·
`docs/features.md` · `docs/baselines.md` · `docs/data_sources/` (licensing, xG) · `docs/adr/` · `docs/remediation/`

## Status
Sprints 0–3 done and remediated (`reports/remediation/FINAL_S0_S3_REMEDIATION_REPORT.md`). Next: Sprint 4 (Elo)
— only after the remediation report's blockers/decisions are closed. Data source licensing is RESEARCH_ONLY.
