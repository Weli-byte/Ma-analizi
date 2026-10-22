# ADR 0070 - Europa League and Conference League fixtures and results from ESPN's public scoreboard

Date: 2026-10-23. Status: accepted (research use). Builds on ADR 0055 (cups in the product path) and 0058/0059 (live results store).

## Context
The app made no forecast for the Europa League or the Conference League on 2026-10-22. Cause (checked, not assumed): ADR 0055 takes the cup schedule from The Odds API events, and the probe-cups workflow
(run 39859449706, 2026-10-23) shows `UEL key soccer_uefa_europa_league: NOT LISTED` and `UECL key soccer_uefa_europa_conference_league: NOT LISTED`: the provider no longer lists both competitions
(only the Champions League is listed). football-data.org answers HTTP 403 for the Europa League with the free key, and openfootball has no league-phase file for 2025-26.

## Decision
1. `src/ingestion/espn_fixtures.py` reads ESPN's keyless public scoreboard (`uefa.europa`, `uefa.europa.conf`; verified live: 18 events per matchday, finished matches with scores, scheduled matches up to the next matchday).
   The scoreboard answers one day per request (a date range answers HTTP 400), so a window is scanned day by day (4 days back, 14 days ahead; 36 requests per competition per run, no key, no credits).
2. `src/markets/live_results.collect_espn` (called by the existing `python -m src.markets.live_results` step of `markets-cloud.yml`) stores finished league-phase results (a knockout score may include extra time, so it is refused,
   like the odds-provider scores) and upcoming fixtures (`espn-<id>`) in the existing `artifacts/results/<league>.json` stores. The existing `fixtures_with_fallback` already turns `upcoming` into forecasts; an upcoming entry of another source
   is never replaced (only `espn-` entries are refreshed; a duplicate of an odds-store fixture is not listed twice).
3. Clubs: names are resolved only through the directory (approved aliases and exact/normalised names; `espn` joined the consulted sources). Eight ESPN display names were approved as aliases for clubs that have history under another spelling
   (Ferencvaros, Hapoel Be'er, Jagiellonia Bialystok, Omonia Nicosia, Union St.-Gilloise -> the id with the larger history, Olympiacos -> the id with the larger history, Ajax Amsterdam, Kairat Almaty; owner to re-confirm).
   A club the directory does not know is skipped and REPORTED in the run line (policy of ADR 0055: no forecast for a club the model has never seen; nothing is registered automatically).
4. Licence: the ESPN endpoint is unofficial and its terms are unverified: RESEARCH_ONLY, like the ESPN odds collector of ADR 0030. Only match lists (clubs, kick-off, final score) are read; no odds, no lineups.

## Result (2026-10-23, local run)
Europa League: 36 matches in the window, 14 new results, 15 upcoming fixtures resolved, 15 forecast artifacts written for 2026-11-05. Conference League: 36 matches, 10 new results, 7 upcoming fixtures resolved, 7 forecast artifacts.
Unresolved clubs (no history, so no forecast): Europa League: Ararat-Armenia, Levski Sofia, Lillestrom, Torreense; Conference League: AGF, CSU Craiova, Egnatia, FC Thun, Hajduk Split, Iberia 1999, Inter Club d'Escaldes, Kauno Zalgiris, KuPS Kuopio,
Mjallby AIF, Riga FC, SK Brann, Sint-Truidense (Sint-Truidense exists in the directory but has no matches in the history). About 11 of 18 Conference League matches of a matchday therefore still have no forecast.

## Limits and options (owner)
- Matches with a club the model has never seen stay without a forecast. Registering those clubs would give a near league-average forecast with no real information; that reverses ADR 0055/0056 and needs the owner's decision.
- ESPN can change or stop the endpoint without notice; a failing day is counted, never filled in, and a failing source never stops the other sources.
- These cups are NOT in the league-model programme (their 2025-26 data are missing there); this is only the product forecast path.

## Rollback
Remove the `collect_espn` call from `live_results.collect` (or delete `src/ingestion/espn_fixtures.py`); delete the `espn` alias rows by their `2026-10-23` approver text.

## Update 2026-10-23 (same day): clubs without history are forecast, with a label
Owner decision: the clubs the model has never seen are no longer skipped. 16 clubs were registered (provenance `no_history_registered`, owner to re-confirm: AGF, CSU Craiova, Egnatia, FC Thun, Hajduk Split, Iberia 1999,
Inter Club d'Escaldes, Kauno Zalgiris, KuPS Kuopio, Mjallby AIF, Riga FC, SK Brann, Ararat-Armenia, Levski Sofia, Lillestrom, Torreense) and one ESPN alias was added for the existing Sint-Truidense. A forecast for such a club uses the
league-average strength (the existing rate model's behaviour for an unknown club), so it carries almost no club-specific information. Every artifact now has `data_quality.low_confidence = {level: LOW, reason: NO_HISTORY, label:
"Geçmişi yok, düşük güven", teams: [...]}` when a club's decayed match weight is under 2.0 (absent, or a single match so far); the match card shows the label as a tag and the match page lists it with the explanation
(English UI: "No history, low confidence"). Older artifacts have no such field and are rendered as before. The label means "do not rely on this number", not that the club is weak. Result: 18 + 18 upcoming fixtures resolved for 2026-11-05/06
(Europa League, Conference League), all clubs resolved.
