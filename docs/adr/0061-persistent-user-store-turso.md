# ADR 0061: Persistent user store on Turso (free tier)

Status: accepted (2026-10-15). Verified live 2026-10-15 (see Live check).

## Problem
On Vercel the user store (favourites, the minimal account record) is a SQLite file in `/tmp` and resets whenever the serverless
function restarts. Signed-in users lose their favourites.

## Options compared (free tiers, no credit card; figures from each vendor's pricing page on 2026-10-15)
| | Turso (libSQL) | Neon (Postgres) | Supabase (Postgres) |
|---|---|---|---|
| Free storage | 5 GB, 100 databases | 1 GB per project | not re-checked |
| Dialect | SQLite: the store's SQL is unchanged | Postgres: SQL and driver change | Postgres |
| Client | HTTPS JSON API, stdlib `urllib`, no new dependency | needs a driver (psycopg) in the lockfile and the Vercel bundle | driver or REST |
| Idle behaviour | not stated on the pricing page | compute suspends after 5 minutes (cold start on the next call) | not re-checked (the free plan is known to pause inactive projects; not verified here) |

## Decision
Turso. The store already speaks SQLite, so the same statements run on both backends; the HTTPS API needs no new package, which
keeps `requirements.lock`, the CI lock check and the Vercel bundle untouched, and the stdlib default TLS context verifies
certificates (the project never disables TLS verification).

## Behaviour (`src/web/store.py`)
- `FORECAST_TURSO_URL` set: Turso backend (`libsql://...` or `https://...`; plain http is refused). `FORECAST_TURSO_TOKEN` is
  required; a URL without a token raises `StoreError` at start. There is no silent fallback to the SQLite file.
- Not set: the SQLite file as before (`FORECAST_USER_DB` or `artifacts/users.sqlite`); local development and tests are unchanged.
- The tables are created on first use (`CREATE TABLE IF NOT EXISTS`), so a database outage does not stop the app from starting;
  a Turso error surfaces as `StoreError` on the request that needed the store.
- Data stored is unchanged (ADR 0053): uid, provider, name, optional e-mail, timestamps, favourite club ids. The privacy page now
  names Turso as a processor located abroad (KVKK transfer disclosure).

## Tests
`tests/test_user_store.py` (SQLite behaviour; the request and response encoding against Turso's documented JSON shapes; TLS
requirement; fail-loud misconfiguration). The real network round trip is `tests/integration/test_turso_live.py`
(`pytest -m live`), which fails without credentials; it has NOT been run yet.

## Consequences
Free-tier limits (5 GB, 500M rows read and 10M rows written per month) are far above a favourites table. A paid host or a
commercial launch is a separate decision (ADR 0036).

## Live check (2026-10-15)
With the owner's Turso database configured in Vercel: guest sign-in, added a favourite club on the live site, redeployed with
`markets-cloud` (a new deployment, so `/tmp` would be empty), and the same session still listed the favourite. The test account was then
deleted with the delete-my-data action. `tests/integration/test_turso_live.py` was NOT run (the Turso token is not in the local `.env`).
