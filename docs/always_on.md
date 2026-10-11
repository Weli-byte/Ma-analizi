# Running without your PC (always-on checklist)

What needs a trigger, and where it runs:

| Job | Where | Needs your PC? |
|---|---|---|
| Match intelligence (scores, goals, tips, track record) | GitHub Actions `markets-cloud.yml` (cron every 4 h, coarse) | No |
| Exact odds (The Odds API) | GitHub Actions `odds-exact.yml` | No, but GitHub's own cron runs only about every 6 h |
| S13 stage locks + LLM, S14 live ticks, paper ledger | Windows tasks on your PC | Yes (research track, not needed to launch) |

## Update 2026-10-11: the odds workflow now restarts itself (ADR 0049) - no setup needed
Each `odds-exact` run collects every 10 minutes for about 50 minutes and then starts the next run with the
workflow's own token, so the 10-minute cadence no longer depends on GitHub's throttled cron or on your PC.
Stop it by setting the repository variable `ODDS_LOOP` to `off` (Settings -> Secrets and variables -> Actions ->
Variables). The cron-job.org route below is only a backup now.

## Optional backup: external clock
GitHub's `*/10` cron is throttled, so use a free external clock that calls GitHub's API:
1. GitHub → Settings → Developer settings → Fine-grained tokens → new token. Repository access: ONLY
   `Weli-byte/Ma-analizi`. Permission: Actions = Read and write (nothing else). Copy it once.
2. Create a free account at cron-job.org (or any scheduler that can send an HTTPS POST).
3. Job 1 (every 10 minutes): `POST https://api.github.com/repos/Weli-byte/Ma-analizi/actions/workflows/odds-exact.yml/dispatches`
   headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, body `{"ref":"main"}`.
4. Job 2 (every 2 hours): same URL with `markets-cloud.yml`.
5. Check Actions: runs should appear every 10 minutes. The token only starts workflows; keep it secret and rotate it
   if it leaks. Never put it in the repository.

## Until that is done
Keep the PC on during match windows (the next rounds are 10-12 October, afternoons/evenings UTC). The PC tick also
dispatches the workflow every 10 minutes while it is on.

## What stays PC-only
S13 locked stages with LLM calls and the S14 live ticks. They are the research/prospective-evaluation track; the
product path (intelligence, odds, API) does not depend on them. An always-on Linux host (e.g. a free-tier VM) could run
`scripts/*_tick.ps1` equivalents; that is a hosting decision for later.
