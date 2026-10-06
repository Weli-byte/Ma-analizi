# 02 — Data

- Current real dataset (strict run, as_of 2026-09-25): **`dv-6f3af90c6bdb`**, 16 raw files, 5350 fixtures, EPL + LALIGA,
  seasons 2019-20 … 2026-27. Season status: 2019-20 … 2025-26 `historical_complete` (380/380 per league-season);
  2026-27 `current_partial` (EPL 10, LALIGA 20 matches).
- Quality gate: 21 checks (Q01–Q20, Q18b) → **0 error failures, 0 warning failures** (one reviewed source anomaly is
  acknowledged in `configs/data.yaml`: LaLiga 2025-26 Mallorca–Barcelona aggregate closing odds overround 0.929).
- Provenance: every file has a sidecar (`origin`, `source_url`, `retrieved_at_utc`, `sha256`). Origins: 13 `archive`
  (Wayback Machine copies) and 3 `manual` (owner browser downloads); none is labelled `official`.
- Checksums: all 16 are `pinned_observed` (drift detection). **None is `verified_official`** — the provider's site
  failed TLS verification, so nothing could be independently confirmed. This is a known limitation, not hidden.
- Downloader: verified TLS only; primary (official) failed with a certificate error in this environment, the
  explicitly configured archive fallback was used and labelled `origin=archive`.
- Content-derived version: `dv-<12 hex>` from raw checksums + normalization inputs (ADR 0003). Atomic pipeline with
  `CURRENT.json`; failure injection at all 7 stages plus a simulated crash leave the previous dataset untouched.
- Team identity: 65 teams / 65 aliases, `validate` clean. Seven promoted-team aliases were approved by the assistant
  (`approved_by` says "owner review pending").
- Timestamps UTC-aware (`TIMESTAMPTZ`), DST/midnight/year-boundary tests pass; `result_available_at_utc` is INFERRED.
- Odds: `pre_match`/`closing`, `timestamp_quality=unknown` for 100% of rows; `Avg`/`Max` are aggregates.
- Licensing: `RESEARCH_ONLY` (docs/data_sources/licensing.md); no explicit terms found — owner action required.
