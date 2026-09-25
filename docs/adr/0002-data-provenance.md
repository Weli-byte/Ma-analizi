# ADR 0002: Data provenance and raw-file integrity

## Status
Accepted — 2026-09-26 (S0–S3 remediation).

## Context
The S1 manifest labelled every file `football-data.co.uk` although seven files were Wayback Machine
copies and three were downloaded by hand; `retrieval_time` was the registration time; no expected
checksums existed; a 0-byte file and HTML error pages were downloaded during development.

## Decision
1. Every raw file has a provenance sidecar (`data/provenance/football_data/<file>.json`, committed) with
   `origin` (`official | archive | manual | other`), `source_url`, `retrieved_at_utc`, `sha256`, `note`.
   Missing provenance is recorded as `origin=unknown` and is FATAL in RESEARCH/STRICT/FINAL runs.
2. The manifest (`src/data/manifest.py`) records per file: dataset_id, source_url, origin,
   retrieved_at_utc, sha256, size_bytes, season, league, filename, schema_hash, parser_version,
   encoding, row count, expected-checksum status.
3. `data/expected_checksums.json` holds checksums with an explicit status:
   `verified_official` (independently confirmed with the provider), `pinned_observed` (recorded when first
   seen: detects drift, does NOT prove authenticity) or absent = `unknown`. Observed values are never
   presented as expected values. A mismatch raises `ChecksumMismatch`; STRICT/FINAL require known status.
4. Raw content is validated before it is trusted: empty file, HTML, invalid header, wrong `Div`,
   truncated/malformed rows, non-UTF-8 encoding (warning).
5. Archive copies are never labelled as official retrievals.

## Alternatives
- Trust filenames only (status quo) — rejected: silently accepts corrupt files.
- Commit raw CSVs — rejected for now: license unclear (see docs/data_sources/licensing.md).

## Consequences
Reproducibility on another machine requires re-downloading files whose checksums match the pins.
All 16 current files are `pinned_observed`; upgrading them to `verified_official` needs an independent
comparison against the provider's site once its TLS problem is resolved.
