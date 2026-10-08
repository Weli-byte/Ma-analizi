# ADR 0036 — Commercial readiness (Phase O)

Status: accepted · 2026-10-08

- Provider terms were re-checked (provider_evaluation.md). Several pages could not be read automatically
  (HTTP 403 / 404 / TLS errors); those are recorded as NOT verified, never assumed permissive. Every source
  remains `RESEARCH_ONLY`; commercial launch is gated on written terms (owner decision).
- Product tiers (Free/Pro/API/B2B) are defined in `docs/product.md`; no betting-advice or guaranteed-win language.
- Added a non-root, hash-pinned Docker image for the API, a verified backup script and the deployment runbook.
  No new forecasting features (stability, security, reproducibility first, per the sprint plan).
