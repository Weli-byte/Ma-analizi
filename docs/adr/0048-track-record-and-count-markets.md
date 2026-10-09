# ADR 0048 — Live track record; corners/cards/shots source decision

Status: accepted · 2026-10-09

## Track record
`python -m src.markets.track` (also a step of markets-cloud.yml) scores the published artifacts against the real
openfootball results: the LAST artifact generated before kickoff per match (never one made after), 1X2 log loss / Brier
/ RPS vs the league base rate known before the first forecast, vs the exact market where one existed, over/under 2.5,
BTTS, correct-score hits and how often the tips came true per lean label. It says "noise" below 100 matches.
Served by `GET /v1/track-record` and the dashboard. This is the prospective evidence the product can show honestly.

## Corners / yellow cards / shots on target (searched 2026-10-09)
- DataHub republishes the football-data.co.uk CSVs under a "PDDL" label, but the page itself says to review Football
  Data UK's terms; a re-publisher cannot grant rights it does not own. Not used.
- Highlightly (highlightly.net): terms 6.1 "Distribution, transfer, and storage of the data ... are allowed. You are free
  to use the data in your applications and products"; no reselling/proxying the API; Basic (free, 100 requests/day) "is
  not subject to the same terms as paid plans"; PRO is USD 9.49/month (7,500 requests/day); statistics are listed for all
  plans. Best candidate; response shape and historical depth are NOT verified yet (needs an API key).
- TheStatsAPI: from USD 50/month, commercial products allowed, 7-day trial.
- football-data.org Statistics add-on: EUR 15/month, terms silent on commercial use (see licensing.md).
Decision: keep these markets out of the product until a Highlightly key is available to verify real responses; then
back-fill history within one PRO month and add the adapter. No fake or imputed statistics in the meantime.
