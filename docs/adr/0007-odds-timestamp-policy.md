# ADR 0007: Odds timestamp semantics

## Status
Accepted — 2026-09-26.

## Context
football-data odds carry no snapshot time. S1 stored them as `pre_match_unspecified`/`closing` with a
NULL timestamp, listed `Avg`/`Max` as if they were bookmakers, and S3 used closing odds as a "market
baseline".

## Decision
`odds_snapshots` columns: `source, bookmaker (NULL for aggregates), market_source_type
(bookmaker|aggregate), aggregate_kind (avg|max|NULL), market, selection, price, snapshot_type
(opening|pre_match|closing|live), timestamp_utc, timestamp_quality (unknown|approximate|exact)`.
- football-data pre-match odds: `pre_match`, timestamp unknown. Closing odds: `closing`, timestamp unknown.
  `Avg`/`Max` are `aggregate`, never bookmakers.
- Only `timestamp_quality = exact` odds may be used for edge/EV/CLV/paper trading (S15).
- The `market_implied` baseline is a **REFERENCE_MARKET_BASELINE** (`model_class`): a reference bar for
  probability quality that uses closing odds; it is reported separately and never interpreted as a
  time-aligned signal or a competitor to beat.
- ROI/CLV are not implemented as historical metrics until timestamped odds exist.

## Alternatives
Drop odds entirely (rejected: closing-odds reference is scientifically useful).

## Consequences
S15 needs a timestamped odds source (S12 provider).
