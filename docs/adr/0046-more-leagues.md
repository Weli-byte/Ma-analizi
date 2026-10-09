# ADR 0046 — Bundesliga, Serie A, Ligue 1 (and Süper Lig, source-limited) in the product path

Status: accepted · 2026-10-09 (owner request: top-5 leagues + Süper Lig)

- `FILES` in `src/ingestion/openfootball.py` now lists EPL, LaLiga, Bundesliga (de.1), Serie A (it.1), Ligue 1 (fr.1)
  and Süper Lig (tr.1). 114 clubs and 135 openfootball aliases were added (name variants such as "Bayern München" /
  "FC Bayern München" merged by hand, `fuzzy_approved`, owner to re-confirm). The pipeline still never auto-registers
  a club: an unresolved name is skipped and counted.
- **Süper Lig is limited by the source, not by the code:** openfootball publishes tr.1 only for 2019-20, 2020-21,
  2024-25 and (partly) 2025-26; the 2021-24 files and the current 2026-27 file return 404. A 404 is a reported gap
  (`missing_season_files`), not a failure. With no current-season file there are NO Süper Lig fixtures, so no
  predictions until the source publishes them. No licence-clean alternative exists (football-data.org's free tier
  has no Süper Lig; ESPN has no terms).
- Evidence with five leagues (validation 2022-24, 3578 matches, final test not downloaded): correct score -0.118 nats,
  1X2 log loss 0.9892 vs 1.0718, over/under 2.5 0.6778 vs 0.6918, BTTS 0.6855 vs 0.6913 (now a clear improvement).
- Odds (The Odds API) are NOT yet collected for the new leagues: sport keys, ESPN codes and the
  football-data.org/The Odds API aliases for the new clubs still have to be set up (credits: 500/month on the free
  plan). Until then their headline 1X2 is the model alone (the market was better than the model in ADR 0041).
