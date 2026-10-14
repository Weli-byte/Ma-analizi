# ADR 0058: match detail tabs, live results, track-record listing

Date: 2026-10-14. Status: accepted.

## Why
1. The match page was one long scroll. Betting apps (Misli was inspected on 2026-10-14) split a match into tabs and collapsible
   market categories: odds categories, then statistics (summary, table, head-to-head, recent matches, injuries/suspensions, ...).
2. The open history (openfootball) stopped at 2026-09-20 while the date was 2026-10-14: the model did not know three weeks of
   results and the track record could score no forecast at all (0 matches).

## Decisions
- **Match page tabs** (`pages.match_page`): Özet, Marketler (collapsible categories: match result, double chance, draw no bet,
  handicap, total goals over/under, both teams to score, team goals, scores, total goals, corners/cards/shots when published),
  Form ve maçlar (recent matches per team with the competition, head-to-head), Puan, Eksikler, Tavsiye, Veri. Same probabilities as
  before, only organised; nothing invented. Tabs without data say so (no league table for a cup in the running season; a team
  whose newest match is older than 21 days is flagged). Not shown because the project has no such data: line-ups, referee,
  player statistics.
- **`details` in the artifact** (`src/markets/details.py`): recent matches (8), head-to-head (8), league table, computed from the
  history at the artifact's cutoff (never from after it). Older artifacts render with a note.
- **Live results** (`src/markets/live_results.py`, cloud step before the forecasts): football-data.org free tier for PL, PD, BL1, SA,
  FL1, DED, PPL and CL (90-minute score; `regularTime` after extra time), The Odds API `/scores` (2 credits, only when a stored
  fixture kicked off 2.5+ h ago without a result) for Süper Lig, Europa League and Conference League. A score from the odds provider
  is accepted only in the league phase (before February): a knockout score may include extra time. Stored in
  `artifacts/results/<league>.json` with `first_seen` (when WE first observed it = when it became available), merged into the history
  by `load_world` unless the open source already has the match, and never loaded by an evaluation (`end_year` set).
  Clubs the directory cannot resolve are counted and skipped (e.g. Sabah, Viking: no history).
- **Track record** lists every scored match (date, competition, score, our 1X2 pick with its probability, most likely score, 2.5
  over/under, both-teams-to-score, each marked hit or miss), hit rates with 95% Wilson intervals, and two naive baselines on the same
  matches (always home, always over 2.5). Only forecasts generated before kickoff count. With few matches the page says the numbers
  are noise (threshold 100).
- Logo lookup list now includes the 222 foreign cup/domestic clubs (cup participants first); the daily alias-logos workflow works
  through it within its request budget.

## Limits
- Austria (and other leagues no free source covers) still has no current-season results: the teams' strength comes from older seasons
  and the page says so.
- The score of a match that goes to extra time in a knockout round is not used from the odds provider.
