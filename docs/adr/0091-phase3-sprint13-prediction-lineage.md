# ADR 0091 — Phase 3, Sprint 13: production reality audit and prediction lineage (2026-10-30)

Plan: `Futbol_Tahmin_Platformu_Faz3_Sprint_13_18_ULTRA_PRO_MAX.pdf`, sprint 13 (the numbering continues the league and Phase 2 plans; it is unrelated to the old S13 snapshot engine of ADR 0027). Full report: `docs/phase3/sprint13_report.md`.

## Decision
- Every NEW published forecast carries a `lineage` block (schema `lineage-v1`: code commit, pipeline, stage, minutes to kick-off, history data version and size, odds source without prices, calibration state, line-up / injury status), written before the content hash so the hash covers it. Old forecasts are not back-filled; they read `legacy_unknown`.
- `GET /v1/fixtures/{id}/lineage` (API key) lists every stored version with its re-verified hash; the match page shows a short "Tahmin künyesi" line (no bookmaker name on the page, as before).
- `python -m src.lineage.smoke` (real, credit-free, redacted provider calls), `python -m src.lineage.inventory` (what runs, from real sources) and `python -m src.lineage.baseline` (the plan's 138-match set frozen from the 2026-10-27 record and verified against later records; VERIFIED_BASELINE true on 2026-10-30).
- Release decision CONDITIONAL_GO: external blockers (API-Football suspended, Highlightly unreachable from the owner's network, odds quota below reserve) and the post-deploy lineage-coverage check remain.

## Not done
No database (the forecast store is files; immutability is the no-overwrite rule plus the hash); no Vercel deployment id (needs the token, which exists only as a repository secret); no property-based test library added (no new dependency).

## Amendment 2026-10-31 — post-deploy coverage
The first cloud run after the deploy (`markets-cloud` 41201300488) wrote 80 forecasts; all 80 carry the lineage block (commit `e4bdd649`) and verify their content hash. The open item of the release decision is closed.
