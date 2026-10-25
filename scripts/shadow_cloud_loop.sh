#!/usr/bin/env bash
# Shadow rounds inside ONE workflow run (ADR 0076): GitHub's cron is delayed or skipped under load, and a pre-match record can only be written in a 3-hour window, so the job runs
# `rounds` rounds `interval` seconds apart and the workflow then starts itself again (the same self-restarting loop as odds-exact, ADR 0049).
# Usage: bash scripts/shadow_cloud_loop.sh <rounds> <interval_seconds>
# State: the append-only stores live in artifacts/league_models/rollout and are published to the `shadow-data` worktree (shadow-data-wt) after a round that changed them,
# and at least every third round (the stamp in shadow_gate.json is the dead-man switch of the markets job).
set -u
rounds="${1:-5}"
interval="${2:-600}"
store=artifacts/league_models/rollout
sig() { { cat "$store"/prospective.jsonl "$store"/outcomes.jsonl "$store"/score_forecasts.jsonl 2>/dev/null; find artifacts/lineups -name 'lineup-*.json' 2>/dev/null | sort; } | sha256sum | cut -d' ' -f1; }

publish() {
  mkdir -p shadow-data-wt/artifacts/league_models
  rm -rf shadow-data-wt/artifacts/league_models/rollout shadow-data-wt/artifacts/lineups
  cp -r "$store" shadow-data-wt/artifacts/league_models/
  [ -d artifacts/lineups ] && cp -r artifacts/lineups shadow-data-wt/artifacts/
  rm -f shadow-data-wt/artifacts/league_models/rollout/dashboard.html
  ( cd shadow-data-wt \
    && git config user.name "shadow bot" && git config user.email "shadow-bot@users.noreply.github.com" \
    && git add -A artifacts \
    && { git diff --cached --quiet && echo "nothing new" || { git commit -q -m "shadow: $(date -u +%Y-%m-%dT%H:%M:%SZ)" && { git push -q data HEAD:shadow-data || { git pull -q --rebase data shadow-data && git push -q data HEAD:shadow-data; }; }; }; } )
}

for r in $(seq 1 "$rounds"); do
  echo "=== round $r of $rounds at $(date -u +%H:%M:%SZ) ==="
  before="$(sig)"
  echo "--- lineups ---"
  python -m src.markets.lineups || echo "lineups: failed (information only, the round continues)"
  for cmd in shadow reconcile monitor scorecard gate notify; do
    echo "--- $cmd ---"
    python -m src.league_models.rollout "$cmd"
    echo "exit=$?"
  done
  after="$(sig)"
  if [ "$before" != "$after" ] || [ $(( (r - 1) % 3 )) -eq 0 ]; then
    publish || echo "publish failed (the next round tries again)"
  fi
  [ "$r" -lt "$rounds" ] && sleep "$interval"
done
publish || true
exit 0
