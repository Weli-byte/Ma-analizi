#!/usr/bin/env bash
# Rollback drill of the public site (ADR 0080). Reads the project's production deployment and its predecessors from the Vercel API, checks the live site, and with `execute`
# rolls production back to the previous deployment and promotes the original one again, verifying after every step which deployment is the production one and that the site answers.
# Usage: bash scripts/vercel_rollback_drill.sh [dry-run|execute|preview]
# `preview` builds the site bundle exactly like the markets job and deploys it as a PREVIEW (not production), then waits until Vercel reports it READY: a check that the bundle builds and deploys
# before it goes to production. (Preview URLs are behind Vercel authentication, so the check is the deployment state, not an HTTP call.)
# Needs VERCEL_TOKEN, VERCEL_PROJECT_ID (and VERCEL_ORG_ID: a "team_..." id is sent as teamId). SITE_URL defaults to the public address. Never prints the token.
set -uo pipefail
mode="${1:-dry-run}"
site="${SITE_URL:-https://mac-tahmin-eight.vercel.app}"
: "${VERCEL_TOKEN:?VERCEL_TOKEN is not set}"
: "${VERCEL_PROJECT_ID:?VERCEL_PROJECT_ID is not set}"
team=""
case "${VERCEL_ORG_ID:-}" in team_*) team="&teamId=${VERCEL_ORG_ID}";; esac
api() { curl -fsS --max-time 30 --retry 3 --retry-all-errors -H "Authorization: Bearer ${VERCEL_TOKEN}" "https://api.vercel.com$1"; }
production_id() { api "/v9/projects/${VERCEL_PROJECT_ID}?${team#&}" | jq -r '.targets.production.id // empty'; }
health() { curl -sS --max-time 30 -o /dev/null -w '%{http_code}' "${site}/v1/health"; }
fail() { echo "::error::$*"; exit 1; }

if [ "$mode" = "preview" ]; then
  python3 scripts/make_vercel_bundle.py --out dist/preview >/dev/null || fail "the site bundle could not be built"
  url="$(cd dist/preview && npx --yes vercel@latest deploy --yes --token "$VERCEL_TOKEN" ${VERCEL_ORG_ID:+--scope "$VERCEL_ORG_ID"} | tail -1)"
  [ -n "$url" ] || fail "the preview deployment printed no URL"
  host="${url#https://}"
  for i in $(seq 1 30); do
    st="$(api "/v13/deployments/${host}?${team#&}" | jq -r '"\(.readyState) \(.target // "preview")"')"
    echo "preview ${host}: ${st}"
    case "$st" in "READY preview"|"READY null") echo "PREVIEW OK: the bundle built and deployed (not production)."; exit 0;; ERROR*|CANCELED*) fail "preview deployment ended as ${st}";; esac
    sleep 10
  done
  fail "the preview was not READY in five minutes"
fi

current="$(production_id)"
[ -n "$current" ] || fail "no production deployment found for the project"
echo "production deployment now: ${current}"
list="$(api "/v6/deployments?projectId=${VERCEL_PROJECT_ID}&target=production&state=READY&limit=6${team}")"
previous="$(echo "$list" | jq -r --arg cur "$current" '[.deployments[] | select(.uid != $cur)][0].uid // empty')"
echo "$list" | jq -r '.deployments[] | "  \(.uid)  \(.url)  created \((.created/1000)|todate)"'
[ -n "$previous" ] || fail "no earlier READY production deployment to roll back to"
echo "previous production deployment: ${previous}"
code="$(health)"; echo "site health before: HTTP ${code}"
[ "$code" = "200" ] || fail "the site does not answer 200 before the drill"
if [ "$mode" != "execute" ]; then echo "dry-run: nothing changed. Everything needed for a rollback is in place."; exit 0; fi

restore() {
  echo "--- restoring the original production deployment ${current}"
  npx --yes vercel@latest promote "$current" --yes --token "$VERCEL_TOKEN" ${VERCEL_ORG_ID:+--scope "$VERCEL_ORG_ID"} >/dev/null 2>&1 || npx --yes vercel@latest promote "$current" --yes --token "$VERCEL_TOKEN" || true
}
trap restore EXIT

echo "--- rolling production back to ${previous}"
npx --yes vercel@latest rollback "$previous" --yes --token "$VERCEL_TOKEN" ${VERCEL_ORG_ID:+--scope "$VERCEL_ORG_ID"} || fail "vercel rollback failed"
sleep 15
now="$(production_id)"
[ "$now" = "$previous" ] || fail "after the rollback production is ${now}, expected ${previous}"
code="$(health)"; echo "after rollback: production is ${now}, site HTTP ${code}"
[ "$code" = "200" ] || fail "the site does not answer 200 after the rollback"
echo "--- promoting the original deployment again"
trap - EXIT
npx --yes vercel@latest promote "$current" --yes --token "$VERCEL_TOKEN" ${VERCEL_ORG_ID:+--scope "$VERCEL_ORG_ID"} || { restore; fail "vercel promote failed (the next markets-cloud run deploys production again)"; }
sleep 15
back="$(production_id)"
[ "$back" = "$current" ] || fail "after the promote production is ${back}, expected ${current}"
code="$(health)"; echo "after restore: production is ${back}, site HTTP ${code}"
[ "$code" = "200" ] || fail "the site does not answer 200 after the restore"
echo "DRILL OK: rolled back to ${previous} and restored ${current}; the site answered 200 at every step."
