# Data quality report — dv1

- raw rows: 3800 | accepted: 3800 | rejected: 0 | duplicates: 0
- leagues: EPL, LALIGA | seasons: 2019-20, 2020-21, 2021-22, 2022-23, 2023-24
- DoD (>=5 seasons, >=2 leagues): PASS

## Rejected by reason

## Invalid values (nulled, not zeroed)

## Unmatched teams (review queue)

## Coverage (fixtures / expected)
- EPL 2019-20: 380/380
- EPL 2020-21: 380/380
- EPL 2021-22: 380/380
- EPL 2022-23: 380/380
- EPL 2023-24: 380/380
- LALIGA 2019-20: 380/380
- LALIGA 2020-21: 380/380
- LALIGA 2021-22: 380/380
- LALIGA 2022-23: 380/380
- LALIGA 2023-24: 380/380

## Known issues
- Odds carry no per-snapshot timestamp (football-data): kind is pre_match_unspecified or closing; do not use pre_match_unspecified odds as time-accurate signals.
- 0/3800 fixtures have no kickoff time; kickoff_utc set to 00:00 UTC of the match day (leakage-conservative).
- 3800/3800 fixtures have odds.
- No xG in this source; xG features unavailable until another source is added.
