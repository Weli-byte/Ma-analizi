# Data pipeline (S1, remediated)

Source: football-data.co.uk CSVs (EPL `E0`, La Liga `SP1`), seasons 2019-20 .. 2026-27 (2026-27 is a
CURRENT_PARTIAL season). Source class: **RESEARCH_ONLY** (docs/data_sources/licensing.md).

## Commands
```
python -m src.data.download [--seasons 2025-26]          # verified TLS, retries, declared fallback
python -m src.data.checksums pin|verify                  # expected-checksum registry
python -m src.data.pipeline --mode strict --as-of 2026-09-25 [--download]
python -m src.data.team_resolution review|suggest|approve|register-team|validate
```
All commands accept `--root DIR` (used by the golden-fixture tests).

## Flow (atomic; ADR 0003)
raw CSV + provenance sidecar + expected checksum → manifest (validate: HTML/empty/truncated/header/Div/encoding)
→ parse & normalize (timezones ADR 0005, teams ADR 0010, odds ADR 0007) → validate → 20-check quality gate
→ write to `data/processed/.tmp-*` → atomic rename to `data/processed/<data_version>/` → atomic
`CURRENT.json` update. Any failure leaves the previous dataset untouched.

## Layers
1. raw: `data/raw/football_data/<DIV>_<YYSS>.csv` (gitignored; license) + `data/provenance/football_data/*.json`
   (committed) + `data/expected_checksums.json` (committed).
2. normalized/processed: `data/processed/<dv>/`: `football.duckdb` and parquet for `leagues, teams, fixtures,
   results, team_match_stats, odds_snapshots`; `dataset_meta.json`, `manifest.json`, `quality_report.{json,md}`,
   `team_mapping.json`.

## Data version
`dv-<12 hex>` derived from raw checksums + normalization inputs (league formats, alias store, parser and
schema versions). Changes iff anything that influences the output changes.

## Season categories
`historical_complete`, `current_partial`, `future_fixture`, and the error state `incomplete_historical`
(a finished season with missing matches fails Q13). Current partial seasons belong to no evaluation split.

## Timestamps
All canonical timestamps are UTC-aware (`TIMESTAMPTZ`). Open datasets with `open_db()` (pins the session
timezone to UTC; otherwise DuckDB returns local time). `result_available_at_utc` is INFERRED.

## Odds
`snapshot_type` pre_match|closing, `timestamp_quality=unknown` for all football-data odds; `Avg`/`Max` are
`market_source_type=aggregate` (bookmaker NULL). Only exact-timestamp odds may drive edge/EV/CLV (S15).

## Quality gate (`quality_report.md`)
Q01–Q20 (+Q18b): columns, encoding, duplicates, dates, scores, FTR consistency, odds > 1, null rates, team
resolution, expected teams / matches per team / matches per season (from `configs/leagues.yaml`, never
hardcoded), season windows, impossible dates, anomalous scores/odds, file size, row-count reconciliation,
schema hash. `error` failures block RESEARCH/STRICT/FINAL; `warning` failures also block STRICT/FINAL.
Reviewed source anomalies can be acknowledged in `configs/data.yaml` (they stay in the report).

## Known limitations
No xG (docs/data_sources/xg.md); odds untimestamped; kickoff times assume UK local time; archive-origin
files are pinned observations, not verified against the provider (site TLS unavailable during remediation).
