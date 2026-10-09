# ADR 0047 — Exact odds for Bundesliga, Serie A, Ligue 1 and Süper Lig

Status: accepted · 2026-10-09

- Real sport keys (read from the live /v4/sports response): soccer_germany_bundesliga, soccer_italy_serie_a,
  soccer_france_ligue_one, soccer_turkey_super_league. `configs/odds.yaml: exact_leagues` lists what the cloud job
  collects (PL, PD, BL1, SA, FL1, TR); `leagues` stays the local ESPN list.
- 74 `the-odds-api` aliases were created from the team names in the real responses (matched by hand, owner to
  re-confirm); two promoted clubs unknown to openfootball were added (Amed SK, Çorum FK).
- BL1, SA and FL1 keep the fixture-window rule (football-data.org free schedule). TR has no free fixture schedule, so it
  is collected at most once per `odds_only_interval_hours` (12 h, about 60 credits/month; free plan 500/month), and the
  Odds API events themselves become the Süper Lig schedule for match intelligence (`fixtures_from_odds_stores`), with
  history from the incomplete openfootball files (ADR 0046). Süper Lig forecasts are therefore lower quality: only
  2019-21 and 2024-26 seasons exist in the history, and the two promoted clubs get neutral strength and a data-quality
  flag.
