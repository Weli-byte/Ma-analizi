# ADR 0032 — S16 MLOps: registry, monitoring, alerts, retrain gate, results ingestion

Status: accepted · 2026-10-06

## Why now (evidence from the real system, 2026-10-02 .. 2026-10-06)
- The scheduler logs have a 4-day hole (2026-10-02 .. 2026-10-06): the PC was off, so no live match,
  odds or stage window was recorded. Nothing noticed it.
- The research dataset's newest result is 2026-08-27 (40 days old) and its primary source
  (football-data.co.uk) was unreachable from this machine (only a Wayback snapshot of 2026-09-01).
- Provider calls were timing out intermittently (live log) with no summary anywhere.

## Decision
- **Operational log** (`src/mlops/oplog.py`): every data-provider HTTP call goes through
  `logged_urlopen` (football-data.org, ESPN, FPL, OpenLigaDB, ESPN lineups) which records provider,
  endpoint label (no URL, no key), ok/status, latency, bytes. Scheduled entry points write a
  `heartbeat` (snapshot / live / odds). LLM calls are monitored from their existing `calls.jsonl`.
- **Monitors** (`monitor.py`, thresholds in `configs/mlops.yaml`, validated by `MlopsConfig`):
  data freshness (FRESH/WARNING/STALE/FAILED), per-provider health (requests, error rate, p50/p95,
  last success/failure, freshness), scheduler heartbeat gaps, LLM health (success/failure, 429 / 5xx
  / schema-failure rates, latency, tokens, estimated cost, mean response size), prediction
  distribution + PSI drift, feature missingness + drift (z-score vs the training features), settled
  metric drift (log loss vs the reference). A monitor with too little data answers
  `INSUFFICIENT_DATA` / `NO_DATA` / `NO_REFERENCE`, never an invented verdict.
- **Alerts** (`alerts.py`): critical for STALE/FAILED data, providers, scheduler; warning for the
  rest; appended to `artifacts/ops/alerts.jsonl`. An alert changes nothing automatically.
- **Registry** (`registry.py`): data versions, feature versions (+ registry hash, parquet checksum),
  every model id/version/class, LLM provider/model + prompt id/version/schema version, calibration
  method and fitted temperatures per LLM model, all artifact directories; content-hashed, changes
  appended to `registry_history.jsonl`.
- **No automatic retraining.** `retrain_gate.py` only reports whether retraining WOULD be allowed:
  new data version, a validation report for it, feature lineage + leakage audit, and an explicit
  human approval file. Nothing triggers a retrain.
- **Report** (`python -m src.mlops.report`): `artifacts/ops/report.{json,md}` including a checklist
  of real-world verifications that can only happen when real events occur (first real in-play
  payload, first locked stage, first announced lineup, first settled result, exact-odds source,
  Anthropic and API-Football keys).
- **Results ingestion** (`src/ingestion/results.py`): finished PL/PD results from football-data.org
  are stored append-only and merged into the snapshot stages' `MatchHistory`, so form features no
  longer stop at the dataset's last day. Leakage-safe: a result is usable only from the moment it
  was first observed (`first_seen`, never backdated); unresolved teams are counted, never
  auto-registered; matches already in the dataset are not added twice. The research dataset,
  checksums and `data_version` are untouched.
- Scheduling: Windows task `FootballMonitorTick` every 30 minutes (`scripts/monitor_tick.ps1`).

## Limits
Monitoring runs where the scheduler runs: if the PC is off nothing is monitored (the heartbeat gap is
only visible afterwards). Real cloud scheduling (GitHub Actions) needs the workflows on `main`, the
data/state kept somewhere durable, and cron cadence is best-effort (5 min minimum, often late) — a
separate step. Metric drift has no reference value yet (no walk-forward artifacts on this machine).
