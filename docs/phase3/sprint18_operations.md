# Phase 3 sprint 18 — operations handbook (ADR 0097)

On-call is the owner alone (no team). Alerts arrive on Telegram (ADR 0077): **critical** at once, **warning** at most once a week per indicator. The status page is `/durum`; the raw
measurements are `artifacts/markets/slo.json` and `artifacts/markets/_ops/slo_history.jsonl` on the `markets-data` branch of the data repository.

## 1. Service level objectives

| Indicator | Target | Critical at | Source |
|---|---|---|---|
| forecast_freshness_hours | ≤ 2 | > 6 | newest published forecast |
| fresh_forecast_share | ≥ 95 % | | fixtures of the next 48 h with a forecast younger than 3 h |
| stage_completion | ≥ 95 % | | due t-24h / t-90m / t-30m / kickoff records of the last 7 days |
| settlement | ≥ 95 % | | fixtures kicked off 6–72 h ago that are settled |
| odds_freshness_hours | ≤ 24 | | newest exact odds quote |
| calibration_ece_1x2 | ≤ 0.05, or the noise floor at this n | | published 1X2 on the settled record, n ≥ 100 |
| api_p95_ms / api_p99_ms | ≤ 1500 / ≤ 3000 | | 20 real requests to `/v1/health` per hourly run |
| api_availability | ≥ 99 % | < 90 % | same probe |
| run_availability_28d (error budget) | ≥ 95 % of hours with a run | | hourly run history |

Error budget: 5 % of the hours of the last 28 days may miss a run (GitHub's scheduler skips some). When `budget_left` reaches 0, no change other than a fix is released until it recovers.
A target is changed only by an ADR. An indicator without data is NOT_MEASURED and is never counted as met.

## 2. Provider failures (circuit breaker, `src/release/breaker.py`)

| Failure | Behaviour |
|---|---|
| 429 / quota exhausted | circuit open for max(Retry-After, 60 min); never retried |
| 401 / 403 (expired, revoked key, suspended account) | circuit open 6 h; never retried; rotate the key (§5) |
| 5xx, timeout, network | one retry after 2 s for 502 / 503 / 504 / timeouts; 3 consecutive failures open the circuit for 30 min |
| 404, other 4xx | request problem: no count, no retry |
| schema change (payload does not parse) | the parser returns nothing / raises `ProviderError`; the page shows UNKNOWN or the last stored value; counted in `schema_errors` |

Quota-metered providers (The Odds API, Highlightly) are never retried. An open circuit is shown on `/durum` and sent once a day on Telegram. Nothing is invented while a source is down.

## 3. Incident runbooks

Each: **detect → contain → fix → verify → record**.

**Stale odds** (`odds_freshness_hours` breached, drift `freshness:odds`). Contain: nothing to do for users; the headline falls back to the model and says "model only". Check the Odds API quota
(`x-requests-remaining` in the odds job log) and the breaker state. Fix: wait for the monthly reset or add a key (`THE_ODDS_API_KEY_2`); never use non-exact odds for edge / EV. Verify: a new
quote with `quality: exact` in `odds-data`. Today: both free keys below the reserve since 2026-10-19 (owner item).

**Line-up mismatch** (a published XI differs from the one played; a club id not resolved). Contain: line-ups are information only (ADR 0081 / 0086), never in a probability. Fix: add the alias
in `configs/team_aliases.yaml` (owner-confirmed); the stored observation stays as observed. Verify: `python -m src.availability.report`.

**Wrong kick-off** (a fixture with a placeholder or moved time). Detect: `kickoff_time_known: false` (date-only source), `kickoff_moved` in the stage freezer, settlement SLO. Contain: the page
says "saat belli değil"; the stage freezer skips the fixture (TIME_UNKNOWN) until a time is published; settlement joins within ±3 days (ADR 0090). Fix: none needed when the source publishes the
time; for a wrong time, the next forecast carries the new kick-off and the freezer keeps both (ADR 0093).

**Model artifact corruption** (a forecast whose recomputed content hash differs, an unreadable file). Detect: `python -m src.prospective.report` (hash audit), the restore drill, loaders that
fail loudly. Contain: the API serves the newest artifact that verifies; never edit an artifact. Fix: restore the file from the data repository history (§6). Record: postmortem.

**Prediction leakage** (information after the cutoff in a forecast or a stage record). Detect: the leakage gates of ADR 0093 (ODDS_AFTER_CUTOFF), ADR 0096 (`gates.leakage_live`), the S13
lineage block. Contain: exclude the affected records from every score (`partition_clean`), mark them in the report, keep them stored (evidence). Fix: the code path, with a test that would have
caught it. Never delete or rewrite the stored forecast.

**Degraded provider** (breaker open, 5xx bursts, slow answers). Contain: the breaker; the page shows the last stored data with its time. Fix: wait, or switch the source only through an ADR
with its licence checked (§7). Verify: the breaker closes after a success; `/durum`.

**Site down / bad deploy** (`api_availability` critical, the site check workflow fails). Contain: `vercel-rollback-drill` with `execute` rolls production back to the previous deployment
(ADR 0080); or redeploy the last good commit by re-running `markets-cloud` on it. Verify: `/v1/health` names the expected commit (`deployed`, ADR 0094).

## 4. On-call escalation and postmortem

1. Telegram critical → look within the day; warning → within the week.
2. If the cause is outside the project (provider, GitHub, Vercel): note it, wait, verify recovery.
3. If users saw wrong information: fix, then write a postmortem in `docs/incidents/YYYY-MM-DD-<slug>.md`.

Postmortem template:
```
# <date> <title>
Impact: what users saw, from when to when (UTC), how many forecasts / pages.
Detection: which alarm or person, delay from start.
Timeline (UTC): ...
Root cause: ...
What went well / what went badly:
Actions (owner, date): ...
Evidence: commits, run ids, files.
```

## 5. Security review (2026-11-01)

| Control | State | Evidence |
|---|---|---|
| RBAC / least privilege | API: one role (API key holder, read only); web: guest / signed-in / owner (Pro by e-mail, ADR 0062). Every workflow declares token permissions (six read-only blocks added in this sprint). | `src/release/gate.py` security check |
| Secrets | only in GitHub secrets / `.env` (git-ignored); gitleaks on full history in CI with no allowlist (ADR 0078); the deploy stops if a secret value appears in the site bundle | `--require-clean-bundle` |
| Secret rotation | rotate a provider key: create the new key, update the GitHub secret, re-run `markets-cloud`, revoke the old key; Telegram token via BotFather `/revoke`; `FORECAST_SESSION_SECRET` rotation signs everyone out (acceptable) | this handbook |
| Rate limits | API: per-key fixed window (`FORECAST_API_RATE_PER_MIN`, default 60) with Retry-After | gate check |
| Cookies | HttpOnly, SameSite=Lax, Secure behind HTTPS | gate check |
| PII minimisation | user store holds id, provider, name, optional e-mail, favourites; no password, phone or token (ADR 0053 / 0061); account deletion on the profile page | `src/web/store.py` |
| Audit log redaction | provider errors carry the exception class only, never the URL with a key (`scrub`, `notify.send`); the gate scans the call log for key patterns | gate check |
| LLM prompt injection | LLM commentary is not served (no budget, ADR 0024 / 0096 E10); provider text never reaches a prompt as instructions (S8 strict JSON) | NOT_APPLICABLE today |

## 6. Disaster recovery

- Backups: the data repository (`markets-data`, `stage_records`, `odds-data`, `shadow-data` branches) is the off-site copy, written every hour; `scripts/backup_artifacts.py` for a local zip.
- Restore drill (2026-11-01, `docs/phase3/s18_drills.json`): 10,194 files and 9,018 forecasts backed up, restored into an empty directory, every file and every forecast content hash verified,
  0 mismatches, 189 s.
- Migration rollback drill: ADR 0062's `plan` column applied by the app's own code to a pre-0062 table and dropped again; rows and favourites kept; re-applying is idempotent.
- Model rollback: champion pinned in `configs/release/canary.yaml`; a stop criterion rolls back automatically (drill: challenger share 5.6 % → 0 %).
- Deployment: Vercel `dry-run` and `preview` (staging) drills passed (runs 41295811913, 41295820893); `execute` (a real production rollback and restore) is available and was not run in
  this sprint (owner decision: it changes production for a few minutes).

## 7. Licensing and attribution gate

UNKNOWN or CONDITIONAL is never approved. Today (`configs/league_models/source_licenses.yaml`): openfootball domestic and cups PERMITTED; football-data.org, The Odds API, Highlightly
CONDITIONAL; ESPN, API-Football, FPL RESEARCH_ONLY; football-data.co.uk BLOCKED (not used by the product). The research product runs with the attributions shown in the app; raw odds are never
redistributed (only derived probabilities); logos are off by default. **A commercial launch is NO_GO** until every source the product uses is PERMITTED in writing.

## 8. Release checklist and approval form

Before a release that changes what users see:
- [ ] CI green on the commit (GitHub Actions, not assumed)
- [ ] `python -m src.release.gate --ci-status success` → research_release GO or CONDITIONAL_GO with every FAIL explained
- [ ] ADR for any methodological change; the sprint 17 promotion gate for a new model (no challenger without PROMOTION_CANDIDATE)
- [ ] drills of `docs/phase3/s18_drills.json` not older than 90 days
- [ ] `/durum` without a critical objective
- [ ] rollback path written down (previous commit, `vercel-rollback-drill execute`)

```
Release approval
Commit: ____________  Date (UTC): ____________
Change: ______________________________________
Gate output: research_release = ______  commercial_launch = ______
Risks accepted: ______________________________
Rollback: ____________________________________
Approved by (owner): ______________
```
