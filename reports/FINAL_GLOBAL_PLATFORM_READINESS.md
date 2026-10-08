# Final global platform readiness (2026-10-08)

## Status block
```
REAL_AI_READY:            PARTIAL  (OpenAI, Gemini, Groq real and verified; Anthropic NOT_CONFIGURED, no key)
GLOBAL_DATA_READY:        PARTIAL  (free-tier sources only; dataset STALE, see below)
S13_PRE_MATCH:            BUILT, first live stage run pending real time (Malaga-Espanyol t-24h 2026-10-08 19:00 UTC)
S14_LIVE:                 BUILT, verified on replayed real OpenLigaDB matches; first real football-data.org in-play payload unobserved
S15_ODDS:                 exact odds via The Odds API (cloud collector); ESPN odds approximate; paper only
S16_MLOPS:                BUILT (monitors, alerts, registry, retrain gate); scheduler needs the PC on
S17_DASHBOARD:            BUILT (static, ADR 0034)
S18_API:                  BUILT (FastAPI /v1, ADR 0035)
COMMERCIAL_READY:         NO  (every data source RESEARCH_ONLY; written terms missing)
CI_ON_MAIN:               green for the last push (ci run 37802234561); nightly-full-benchmark RED (below)
```

## Verified now
- Local: ruff clean; full test suite passes; overall coverage 91.0% (gate 90%), critical-path gate passed.
- GitHub Actions: `ci` green on main (3.12 + 3.14, lock-up-to-date, real-data-sanity); `live-ai` run
  37752169709 green (real OpenAI/Gemini/Groq/FPL/football-data.org/The Odds API calls).
- API verified over a real socket (uvicorn) against local artifacts; dashboard built from real artifacts.

## Update 2026-10-08 (follow-ups closed)
- nightly-full-benchmark: root cause = upstream E0_2122.csv added a BOM and corrected one half-time score;
  re-pinned (ADR 0037), in-progress 2026-27 files unpinned; nightly run 37808159418 GREEN.
- Docker: slim API image (`requirements-api.lock`, ADR 0040 note in deployment.md) built (467 MB) and run:
  /v1/health ok, 401 without key, data with key, non-root uid 10001, read-only artifact mount.
- First real S13 stage locked (Malaga-Espanyol t-24h, 96 min late because the PC was off; injuries OBSERVED
  from API-Football, lineups UNKNOWN).
- Free commercially usable data: openfootball (public domain) ingested (ADR 0038). Odds/injuries/lineups still
  have no free source with a commercial grant.
- Bet suggestions added at the owner's request (ADR 0039): exact odds only, quarter Kelly capped at 2%,
  confidence at most MEDIUM. Honest context: on 2022-24 validation no model beats the market (log loss 0.9476
  market vs 0.9695 Elo), so suggestions are a research signal, not an edge.
- S19 research report: `docs/research_report.md`.
- Dataset freshness: STALE -> WARNING (newest 2026-09-20; upstream itself ends there).

## Update 2026-10-09 (match intelligence)
- `src/markets/` (ADR 0041): correct scores, 1X2, double chance, handicap, goals O/U, BTTS, team totals, corners,
  yellow cards, shots on target, ranked tips; API `/v1/fixtures/{id}/intelligence`, `/v1/tips`; dashboard section.
- Out-of-sample (2022-24, 1520 matches, final test not loaded): clear skill over baselines for correct score,
  1X2, over/under 2.5, corners and shots on target 8.5-10.5; NO clear skill for BTTS, yellow cards, shots on
  target 7.5. The de-vigged market still beats the model on 1X2 (0.9253 vs 0.9505), so the headline 1X2 is the
  market whenever exact odds exist.
- Cloud generation (ADR 0042): `markets-cloud.yml` ran green (run 37848656607): 119 results ingested, 20
  artifacts published to `markets-data`, PC synced them with `python -m src.markets.run --sync-remote`.

## Still open
1. Data licences for football-data.co.uk / football-data.org / API-Football / The Odds API unverified; commercial
   launch gated. openfootball (public domain) is the only source with an explicit commercial grant.
2. S13 stage locks and S14 live ticks still depend on the PC being on (they need 2-10 minute timing); only odds
   and match intelligence run in the cloud.
3. No S14 real in-play payload, announced ESPN lineup or settled paper bet observed yet (needs real time).
4. API rate limiter is in-process; no TLS/hosting.
5. Anthropic not configured (no key).
6. No odds for corners/cards/correct scores in the exact feed: those markets are probabilities and tips, not
   value claims.
