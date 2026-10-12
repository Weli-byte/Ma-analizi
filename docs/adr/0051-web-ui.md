# ADR 0051 — Public web UI (S19)

Status: accepted · 2026-10-12 (owner asked for the user interface stage)

`src/web/` adds server-rendered Turkish pages to the same FastAPI app: `/` (upcoming matches, league tabs, probability bar,
top tips), `/mac/{id}` (result probabilities, model vs market, top scores, goals, tips, research bet suggestion, data quality),
`/tavsiyeler`, `/basari-kaydi` (live track record), `/hakkinda` (method, evidence, limits, responsible gambling). No JavaScript
framework, no external request, light and dark themes, mobile layout. The pages need no API key (they show derived values only);
`/v1/*` still fails closed without keys. `create_app(..., ui=False)` turns the UI off.
Not shown, on purpose: raw bookmaker prices and names (ADR 0043), club logos (trademarks), player-level injury data
(ADR licensing). Required attribution and "Gamble Responsibly. 18+" are in every footer.
Run locally: `python -m uvicorn src.api.app:create_app --factory --port 8080` then open http://127.0.0.1:8080/.
Not built: accounts, Free/Pro gating, payments, a hosting target (owner decisions).
