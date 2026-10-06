# ADR 0008: Rest-day features

## Status
Accepted — 2026-09-26.

## Context
`rest_days` reached 811 days for teams returning after relegation and mixed summer breaks with match-to-match
rest (114 fixtures > 30 days, 23 > 100 days in the original dataset).

## Decision
Three features per side: `rest_days_raw` (unbounded, for transparency), `rest_days_capped =
min(raw, features.rest_days_cap)` (default 30, configurable) and `season_break_flag` (1 when the last
available match belongs to an earlier season). Models should use the capped value plus the flag.
This is NOT true fatigue: cup, continental and international matches are absent from the data; documented in
the feature spec and here.

## Alternatives
Drop the feature (rejected: useful signal); winsorise silently (rejected: hides the break).

## Consequences
`fv2` replaces `rest_days` from `fv1`; artifacts are versioned by `feature_version`.
