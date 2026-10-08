# ADR 0034 — Dashboard (S17): static, read-only, built from real artifacts

Status: accepted · 2026-10-08

## Decision
- `python -m src.dashboard.build` writes ONE self-contained `artifacts/dashboard/index.html` (inline CSS and
  SVG, light/dark, no external request, no JavaScript). `--serve PORT` serves it on 127.0.0.1 only.
- `src/dashboard/viewmodel.py` is a pure read of artifacts: locked S13 stages, real LLM forecast runs, odds
  stores (local + cloud-collected), live fixtures, the latest historical benchmark evaluation, the ops report.
  No number is hard-coded and nothing is simulated. A section without data says "no data"; an unknown value
  renders as UNKNOWN, never 0.
- Every prediction row shows prediction_id, model, provider, generated_at, information_cutoff, data_version,
  feature_version and prompt_version (LLMs). Data cells carry OBSERVED / INFERRED / UNKNOWN / STALE / FAILED.
- Odds are shown raw with their `timestamp_quality` tag; the dashboard never computes or recommends bets
  (value analytics stay paper-only in `src.odds`). The benchmark section repeats the memorization caveat and
  declares no winner.
- Values are HTML-escaped; the page is not a server, so there is no auth surface. Publishing it publicly is a
  Phase O decision (data licences are RESEARCH_ONLY).
