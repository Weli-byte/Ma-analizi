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

## Not verified / open (honest list)
1. **nightly-full-benchmark is failing** (2026-10-07 and 10-08): `E0_2122.csv sha256 335afc.. != pinned d3487b..`.
   The upstream CSV changed after pinning; the pipeline correctly refuses. Needs an owner decision (inspect the
   change, then re-pin with an ADR). Not silently re-pinned.
2. Dataset STALE (newest result 2026-08-27): primary source unreachable from this network; partial mitigation
   by ingesting recent results.
3. Docker image: Dockerfile written; a local `docker build` did not finish in 25+ minutes (very large locked ML
   dependency set), so the image is NOT verified. Consider a slimmer API-only requirements file.
4. No real S13 locked stage, S14 real in-play payload, announced ESPN lineup or settled paper bet exists yet.
5. Scheduling depends on the PC being on (only the odds collector runs in the cloud).
6. Data licences unverified for every source (`docs/data_sources/provider_evaluation.md`); several vendor
   pages returned 403/404/TLS errors to automated fetch.
7. API rate limiter is in-process (single worker); no TLS/hosting yet.
8. Anthropic: owner adds `ANTHROPIC_API_KEY` later; then run live test and pick the cheapest model from official docs.
9. S19 final research report and demo flow are not written.

## Owner actions
Decide the nightly CSV re-pin; get written data terms or a paid provider; add the Anthropic key; keep the PC on
(or move collectors to the cloud) so real stages and live forecasts accumulate.
