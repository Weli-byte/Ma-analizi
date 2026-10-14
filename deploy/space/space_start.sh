#!/usr/bin/env bash
# Start script of the Space: pull the published forecasts from the PRIVATE data repo (read access through a deploy key kept
# as the Space secret DATA_REPO_DEPLOY_KEY), refresh them every 20 minutes, and serve the app on $PORT.
set -u
cd /app
GITHUB_ED25519="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"   # GitHub's published ed25519 host key fingerprint

setup_ssh() {
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  echo "$DATA_REPO_DEPLOY_KEY" > ~/.ssh/data_key && chmod 600 ~/.ssh/data_key
  ssh-keyscan -t ed25519 github.com > /tmp/gh_key 2>/dev/null
  if ssh-keygen -lf /tmp/gh_key | grep -q "$GITHUB_ED25519"; then
    cp /tmp/gh_key ~/.ssh/known_hosts
    printf 'Host data-repo\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/data_key\n  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n' > ~/.ssh/config
    return 0
  fi
  echo "GitHub host key does not match the pinned fingerprint: not syncing" >&2
  return 1
}

sync_data() {
  rm -rf /tmp/data && mkdir /tmp/data && cd /tmp/data && git init -q \
    && git fetch -q --depth 1 git@data-repo:Weli-byte/Ma-analizi2.git markets-data \
    && mkdir -p /app/artifacts && for part in artifacts/markets artifacts/injuries artifacts/results; do (git archive FETCH_HEAD "$part" 2>/dev/null | tar -x -C /app) || true; done \
    && echo "data synced $(date -u +%FT%TZ)"
  cd /app
}

if [ -n "${DATA_REPO_DEPLOY_KEY:-}" ] && setup_ssh; then
  sync_data || echo "first sync failed: the app starts with no forecasts"
  ( while true; do sleep 1200; sync_data || echo "sync failed"; done ) &
else
  echo "DATA_REPO_DEPLOY_KEY not set: no forecasts to show"
fi
exec python -m uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port "${PORT:-7860}"
