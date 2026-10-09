# ADR 0044 — Licence-clean history for the product path: openfootball instead of football-data.co.uk

Status: accepted · 2026-10-09 (owner: football-data.co.uk is not usable for this application)

## Decision
`configs/markets.yaml: history_source: openfootball` (default). Match history (2019-20 onward, EPL + La Liga) and
the upcoming fixtures for match intelligence come from openfootball/football.json (public domain, ADR 0038).
Team names resolve through the existing alias directory (23 historical clubs added, `fuzzy_approved`, owner to
re-confirm); result availability is `kickoff + result_lag_hours` (INFERRED, same 3h policy as ADR 0006).
`data_version` is derived from the exact bytes used (`dv-of-<hash>`); files are cached locally and a network
failure falls back to the cache and is reported (`history.stale_files`).

## Evidence
Same evaluation as ADR 0041, openfootball only (validation 2022-24, 1520 matches, final-test seasons not even
downloaded): correct score -0.1191 nats vs baseline, 1X2 log loss 0.9741 (vs 0.9740 with the CSV dataset), over/under
2.5 0.6758, BTTS no clear difference. The goals-based markets lose nothing.

## What is lost
openfootball has scores only: NO corners, yellow cards or shots on target in the product path (those columns exist
only in the research-only football-data.co.uk CSVs). They remain available with `history_source: football-data`
for research, never for published output. The cloud workflow no longer needs the CSVs or any API key.

## Still on football-data.co.uk (research only, not part of the product path)
The S4-S11 benchmark pipeline (Elo/Poisson/GBM/ensemble, walk-forward, calibration) and the S13 stage snapshots'
feature rows. They are research; moving them to a licence-clean source is a separate task.
