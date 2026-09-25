# ADR 0001: Monorepo, stack, contracts

Status: accepted — 2026-09-25 (Sprint 0)

## Context
Plan: `docs/reference/Global_Football_Forecasting_MASTER_Sprint_Plani.pdf` (20 sprints). Scientific
validity (no leakage, proper scoring rules) outranks features.

## Decisions
1. **Monorepo**: `apps/{api,dashboard,worker}`, `src/{data,features,models,calibration,evaluation,ensemble,value,monitoring,schemas,config}`, `configs`, `tests`, `docs`, `reports`.
2. **Python >=3.11**, Pydantic v2 for all contracts, YAML configs validated into typed models, ruff + pytest.
3. **Schemas are frozen** (immutable) with validators enforcing: tz-aware UTC, probs in [0,1] summing to 1,
   generated_at >= cutoff, cutoff <= kickoff, feature available_at <= cutoff, version-name regexes.
4. **Deterministic ids/hashes**: `prediction_id`, `config_hash` -> replay/reproducibility.
5. **Secrets**: configs hold env var *names* only; `.env` gitignored; a test scans for key patterns.
6. **Storage** (later sprints): Parquet/DuckDB first, PostgreSQL for live. Streamlit prototype before Next.js.
7. Notebooks never hold production logic.

## Consequences
Later sprints import `src.schemas`; changing a schema needs an ADR + version bump.
Deferred: DB, providers, models (S1+).
