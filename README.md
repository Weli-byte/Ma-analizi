# Global Football Forecasting Benchmark & Intelligence Platform

> Predict the probability. Benchmark the models. Measure the edge.

Output = 1X2 probability distribution, judged by Log Loss / Brier / RPS / calibration. See
`docs/reference/` for the master sprint plan and `CLAUDE.md` for working rules.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.lock -e ".[dev]"
ruff check . && pytest
```

## Layout
`apps/` (api, dashboard, worker) · `src/` (schemas, config, data, features, models, evaluation, ...)
· `configs/` · `docs/` (protocol, leakage policy, ADRs) · `tests/` · `reports/`

## Docs
- `docs/benchmark_protocol.md` · `docs/leakage_policy.md` · `docs/versioning.md` · `docs/adr/`

## Status
Sprints 0-3 done (repo, data dv1, features fv1, baselines). Next: S4 Elo. Docs: `docs/data_pipeline.md`, `docs/features.md`, `docs/baselines.md`.
