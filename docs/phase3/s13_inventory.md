# Phase 3 sprint 13 - inventory of what is running (ADR 0091)

Generated 2026-10-30T21:21:46.833549+00:00 by `python -m src.lineage.inventory`.

## Deployed code
- Newest successful `markets-cloud` run (builds and deploys the public site): {"createdAt": "2026-10-30T20:53:26Z", "databaseId": 41126761377, "event": "schedule", "headSha": "a37395d4dd127ba2b5539382d38d1d3e5bd227d9"}.
- The Vercel deployment id itself is readable only with the Vercel token, which exists only as a repository secret; the run above is the deploy step's own record.

## Data branches (private data repository)
- `markets-data`: {"date": "2026-10-30T21:00:35Z", "sha": "ce70c6ea657ff0aa0326a7ac2cc86c6c5260e88a"}
- `shadow-data`: {"date": "2026-10-30T21:12:59Z", "sha": "fdc1a52e9ec450078715aa990a895f6b054d3d37"}
- `odds-data`: {"date": "2026-10-19T18:53:21Z", "sha": "3790827a7f54d5adb455800a097025e080eab1a6"}

## Versions in the code
- market_model: `markets-1.0.0`
- api: `1.0.0`
- lineage_schema: `lineage-v1`
- User store schema (Turso / SQLite, `src/web/store.py`): `users`, `favorites`, plus the additive `users.plan` column (ADR 0062); no other migration exists.

## Providers

| provider (configs/ingestion.yaml) | enabled |
|---|---|
| football-data-org | True |

Live smoke (`docs/phase3/s13_provider_smoke.json`):

| provider | status | HTTP | latency ms |
|---|---|---|---|
| football-data.org | OK | 200 | 1289 |
| the-odds-api (the_odds_api_key) | UNREACHABLE |  | 615 |
| the-odds-api (the_odds_api_key_2) | OK | 200 | 1600 |
| api-football | ACCOUNT_REFUSED | 200 | 2082 |
| espn (keyless) | OK | 200 | 2032 |
| openfootball (keyless) | OK | 200 | 2196 |
| highlightly | NOT_CALLED_FROM_THIS_NETWORK |  |  |

## Repository secrets (names only)
- API_FOOTBALL_KEY, DATA_REPO_DEPLOY_KEY, FOOTBALL_DATA_ORG_API_KEY, GEMINI_API_KEY, GOOGLE_CLIENT_ID, GROQ_API_KEY, HIGHLIGHTLY_API_KEY, OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, THE_ODDS_API_KEY, THE_ODDS_API_KEY_2, VERCEL_ORG_ID, VERCEL_PROJECT_ID, VERCEL_TOKEN

## Live record
- `track_record.json` generated 2026-10-29T20:43:59.375031+00:00: 231 settled matches scored (prospective only).
