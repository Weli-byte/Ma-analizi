# Dashboard (S17)

`python -m src.dashboard.build` then open `artifacts/dashboard/index.html` (or `--serve 8765`).
Sections: data freshness and API health, upcoming matches (stages, injuries/lineups status, raw odds with
timestamp quality), pre-match probabilities (full traceability), forecast updates, live matches, model and
provider comparison, calibration (temperature + reliability curves), historical performance by league and
season, LLM usage and estimated cost. See ADR 0034.
