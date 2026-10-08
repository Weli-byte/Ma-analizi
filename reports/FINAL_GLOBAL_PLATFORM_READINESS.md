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

## Still open
1. Data licences for football-data.co.uk / football-data.org / API-Football / The Odds API remain unverified
   (vendor pages blocked automated fetch); commercial launch still gated.
2. No S14 real in-play payload, announced ESPN lineup or settled paper bet observed yet.
3. Scheduling needs the PC on (only the odds collector is in the cloud).
4. API rate limiter is in-process; no TLS/hosting.
5. Anthropic not configured (no key).
6. Local PC cannot reach football-data.co.uk; fresh CSVs come from the cloud (CI) only.
