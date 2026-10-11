# ADR 0049 — Self-restarting exact-odds loop

Status: accepted · 2026-10-11 (owner asked for the PC-independent trigger to be done without external accounts)

GitHub's `*/10` cron ran about every 6 hours (measured), so the t-90m / t-30m collection windows were mostly missed.
`odds-exact.yml` now runs `scripts/odds_cloud_loop.sh`: 5 rounds, 10 minutes apart (credits are spent only inside a
fixture window, as before), commits new quotes to the private data repo after each round, and its last step starts the
next run with the workflow's own token (`gh workflow run`; `workflow_dispatch` events created with GITHUB_TOKEN do start
runs). The concurrency group keeps runs sequential, and the old cron stays as a backup if the chain ever breaks.
Kill switch: repository variable `ODDS_LOOP=off`. Cost: free for a public repository (Actions minutes are unlimited
there); if the repository is made private this loop would consume the 2000 free minutes in about two days and must be
stopped first. A manual `force` run does not restart the chain.
