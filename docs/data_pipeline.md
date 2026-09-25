# S1 Data Pipeline

Source: football-data.co.uk CSVs (EPL `E0`, La Liga `SP1`), seasons 2019-20 .. 2023-24.

## Run
```
python scripts/download_football_data.py        # or download manually, save as E0_2324.csv, SP1_2324.csv ...
python -m src.data.pipeline                     # raw -> cleaned -> processed (DuckDB + Parquet) + report
```
Raw files go to `data/raw/football_data/<DIV>_<YYSS>.csv`.

## Layers
1. raw: untouched CSV + `manifest.json` (source, retrieval_time, sha256, league, season).
2. cleaned: per-row validation in `src/data/clean.py`; bad rows rejected with a reason, never zero-filled.
3. processed: `data/processed/<data_version>/` — `football.duckdb` + parquet: leagues, teams, fixtures,
   results, team_match_stats, odds_snapshots; `team_mapping.json` (incl. review queue).
Report: `reports/data_quality_<data_version>.{json,md}`.

## Rules / assumptions
- Rebuild is deterministic; second run yields identical tables (idempotent, tested).
- Team names: alias table `configs/team_aliases.yaml` + normalization. Near-miss names (>=0.85 similar)
  are NOT auto-mapped: they land in the review queue and the row is rejected as `unmatched_team`.
  Add the alias, rerun.
- Kickoff times are UK time -> converted to UTC. Missing time -> 00:00 UTC of match day (leakage-safe).
- Odds: decimal, must be > 1. Bookmaker kept. Source has no snapshot timestamp: kind is
  `pre_match_unspecified` or `closing`; timestamp null. Don't use for time-accurate signals (S15).
- No xG in source. Timestamps stored as naive UTC in DuckDB.
