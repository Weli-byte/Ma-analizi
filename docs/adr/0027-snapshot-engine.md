# ADR 0027 — S13 snapshot engine (T-24h / T-90m / T-30m / kickoff)

Status: accepted · 2026-10-02

## Decision
- **Stages** (`src/snapshot/stages.py`): `t-24h`, `t-90m`, `t-30m`, `kickoff`. Each has a cutoff
  offset BEFORE kickoff and a tolerance window (180 / 20 / 10 / 4 minutes). A stage run outside
  its window is `MISSED` (reported, never run late); at/after kickoff every stage is `MISSED`.
  The `kickoff` stage is the final look at **T-5m**, not T-0: `PredictionRecord` requires
  `information_cutoff <= generated_at <= kickoff`, so a cutoff exactly at kickoff could only be
  generated at the instant of kickoff. (Changed from the earlier draft offset of 0.)
- **Snapshot** (`engine.py`): immutable `StageSnapshot` with leakage-safe features from
  `compute_features` at the stage cutoff, `lineups`/`injuries` = `UNKNOWN (no_provider)`, and a
  content-derived `snapshot_hash` that excludes wall-clock time: same inputs, same hash, same
  system-side prompt payload. No lineup/injury/odds value is ever invented; models that do not
  need them simply run.
- **Predictions**: every configured model (except `market_implied`, which needs exact-timestamp
  odds that do not exist) predicts through `predict_stage`; a model that abstains or raises is
  recorded in `model_status.json`, never filled. Elo gained `predict_proba_pending` (read-only):
  `predict_proba` replays outcomes into its rating state, which is invalid for a fixture without a
  result. Optional REAL LLM predictions (`--with-llm`, `ALLOW_REAL_LLM_CALLS=true`, budget gated).
- **Delta + lock** (`pipeline.py`, `store.py`): delta vs the previous locked stage per model;
  the stage directory (`snapshot.json`, `predictions.jsonl`, `model_status.json`, `deltas.json`,
  LLM calls/responses, `LOCK.json`) is written atomically and is immutable. Re-invoking a locked
  stage returns what is stored. A persistent `PredictionLedger` (`artifacts/snapshots/ledger.jsonl`)
  rejects any conflicting re-prediction.
- **Runner** (`python -m src.snapshot.run`): meant for a scheduler (every ~10 min); exits quickly
  when no window is open. There is deliberately no `--now` override.

## Limitations (stated, not hidden)
- Statistical/ML models are fit on the TRAIN+VALIDATION seasons only (2019-20..2023-24); the
  final-test seasons stay locked (ADR 0004), so Elo/Poisson state is stale for 2026 fixtures and
  the football-data.co.uk history ends 2026-08-27. Revisit after `run_final_evaluation` with an
  ADR (a production refit on all seasons is a separate, explicit decision).
- No scheduler is installed by this repo; the owner must run `src.snapshot.run` periodically.
- No real stage could be produced on 2026-10-02: the next real fixtures kick off 2026-10-09/10, so
  their first window (t-24h) opens 2026-10-08/09. The engine is verified on a REAL historical
  fixture with the clock as a function argument (`tests/test_snapshot.py`); the first live stage
  run is pending real time.
