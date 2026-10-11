#!/usr/bin/env bash
# Cloud loop for the exact-odds collector (ADR 0049). GitHub's own */10 cron runs only about every 6 hours, so one
# workflow run now collects every WAIT seconds for N rounds and the workflow then starts itself again.
# usage: odds_cloud_loop.sh <rounds> <wait_seconds> [--force]    (--force applies to the first round only)
# Expects: odds-data-wt (worktree of the private data repo) and the git remote "data".
set -u
rounds="${1:-5}"
wait_s="${2:-600}"
shift 2 || true
extra="$*"

for i in $(seq 1 "$rounds"); do
  echo "== round $i/$rounds $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python -m src.odds.run collect-exact --data-root odds-data-wt $extra || echo "collect-exact failed (continuing)"
  extra=""
  (
    cd odds-data-wt || exit 0
    git config user.name "odds-exact bot"
    git config user.email "odds-exact-bot@users.noreply.github.com"
    git add -A artifacts
    if git diff --cached --quiet; then
      echo "nothing new"
    else
      git commit -q -m "odds: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
      git push data HEAD:odds-data || {
        git fetch data odds-data && git rebase data/odds-data && git push data HEAD:odds-data
      } || echo "push failed (will retry next round)"
    fi
  )
  if [ "$i" -lt "$rounds" ]; then
    sleep "$wait_s"
  fi
done
