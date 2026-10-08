# Deployment, secrets, backup, monitoring, rollback (Phase O, ADR 0036)

## Environments
| | dev (this PC) | staging | production |
|---|---|---|---|
| keys | `.env` (gitignored) + Windows env | GitHub environment secrets | host secret store / orchestrator secrets |
| artifacts | local `artifacts/` | volume | volume, API mounts read-only |
| LLM calls | gated by `ALLOW_REAL_LLM_CALLS` | gated | gated + budget in `configs/provider.yaml` |
Separate keys per environment; a key in one environment is never reused in another.

## Image
`docker build -t forecast-api .` (uses the slim `requirements-api.lock`, 33 packages; verified 2026-10-08: builds, runs, health + auth + data OK, non-root) then
`docker run -p 8000:8000 -e FORECAST_API_KEYS=<key> -v <artifacts>:/app/artifacts:ro forecast-api`.
Non-root user, hashed requirements, no secret baked in, HEALTHCHECK on `/v1/health`.
Collectors (snapshot/live/odds ticks) are NOT in the image: they run as scheduled jobs (Windows tasks today,
`odds-exact.yml` in the cloud) and write the artifacts the API reads.

## Secrets
Only environment variables / GitHub secrets. `.env` is gitignored; logs, URLs and reports are scrubbed
(`scrub`, `logged_urlopen`). Rotate a key by replacing the secret and restarting; `FORECAST_API_KEYS` accepts
several keys so rotation has no downtime.

## Backups
`python scripts/backup_artifacts.py --out backups` writes a zip of `artifacts/` + `reports/benchmarks/` with a
SHA-256 manifest (never `.env`); `--verify FILE` re-checks it. Run daily (task scheduler / cron) and copy off
the machine. The prediction ledgers and locked stages are irreplaceable (forecasts cannot be recreated after the
fact); datasets and models are rebuildable from code.

## Monitoring
`python -m src.mlops.report` (freshness, provider health, LLM failure/cost, heartbeats, drift),
`MonitorTick` task, `/v1/health`, container HEALTHCHECK. Alerts are written to the ops log.

## Rollback
Images are tagged by git commit (`forecast-api:<sha>`); rollback = run the previous tag. Data is append-only and
locked, so a code rollback never rewrites predictions. Restore state with the backup zip (unzip into the repo
root, run `--verify` first).

## Open items (not done)
Shared rate-limit store for multi-worker, TLS termination/reverse proxy, a hosted deployment target, off-site
backup destination, and the data-licence gate in `docs/data_sources/provider_evaluation.md`.
