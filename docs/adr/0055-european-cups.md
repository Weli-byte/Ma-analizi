# ADR 0055: UEFA Champions League, Europa League, Conference League

Date: 2026-10-13. Status: accepted.

## Decision
The three European cups join the markets product path as leagues `UCL`, `UEL`, `UECL`.

- **History**: openfootball/champions-league (CC0). Verified 2026-10-13: Champions League 2019-20..2025-26,
  Europa League 2020-21..2024-25, Conference League 2021-22..2024-25 (16 files, 2388 matches). The goals models
  use the **90-minute score** (extra time and penalties are not part of the 1X2 market). Kick-off times are CET/CEST
  clock times converted with Europe/Paris (INFERRED, flagged like other inferred times).
- **Clubs**: 45 clubs of the cup files were already known (the six domestic leagues) and are linked by alias; 139 foreign
  clubs are CREATED once (`python -m src.markets.cup_clubs --apply`, id `<COUNTRY>_<slug>`, provenance `cup_import`,
  confidence 0.9, owner to re-confirm). A club spelled differently across seasons becomes two clubs (documented limitation).
- **Fixtures and odds**: no openfootball file exists for the running season, so the schedule comes from The Odds API events
  (`soccer_uefa_champs_league`, `soccer_uefa_europa_league`, `soccer_uefa_europa_conference_league`; the keys follow the
  provider naming and were CONFIRMED on 2026-10-13 against the real `/v4/sports` response and `/events` lists by the cloud probe
  `probe-cups.yml` / `python -m src.odds.probe_cups`, no credits). A failing cup call returns one line and never aborts the other leagues. Cups are collected once per
  `cup_interval_hours` (24 h; about 90 credits/month). Names are resolved WITHOUT a country filter
  (`TeamDirectory.resolve_any_country`): exact match on canonical names and aliases, else a match after removing club-form words
  ("FC", "SK", ...); ambiguous or unknown names are skipped and reported, never guessed or registered.
- **Model**: cups share the multiplicative attack x defence structure; each cup has its own league mean (`mu_home`/`mu_away`).

## Limits (stated, not hidden)
- Foreign clubs are linked to the rest of the graph only through cup matches: their ratings are thin and shrunk toward the mean.
- Neutral-venue finals are treated as normal home/away matches.
- Until the Odds API names of the foreign clubs are mapped, some cup fixtures stay unresolved and produce no forecast.

## Update 2026-10-13: probe result
All three keys are listed by the provider. Of ~100 upcoming cup club names, ~65 resolved at once; 13 got `the-odds-api` aliases
(e.g. Sporting Lisbon, Salzburg, Sparta Prague). Four clubs the cup files spelled two ways were merged into one id
(Sporting CP, Braga, Salzburg, Crvena Zvezda). ~20 clubs (e.g. AGF, Brann, Viking, Omonoia, Levski) never played a cup match
in 2019-2025, have no history, and stay unresolved: no forecast is made for a club the model has never seen.
