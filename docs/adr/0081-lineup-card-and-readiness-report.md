# ADR 0081 - Starting line-up card (information only) and a readiness report

Date: 2026-10-25. Status: accepted.

## 1. Line-up card
* **Source:** ESPN's public match summary (keyless, UNOFFICIAL, RESEARCH_ONLY like ADR 0070). The API-Football account that feeds the injuries card turned out to be SUSPENDED ("Your account is suspended", seen on 2026-10-25; the injuries collection has failed since 2026-10-18), so it could not be used or verified; ESPN was probed live and returned complete line-ups with formations.
* **Collector** (`src/markets/lineups.py`): a line-up exists only once the clubs announce it (about an hour before kick-off), so every shadow-loop round (ten minutes) looks at the matches of the nine competitions that start within the next 100 minutes (until ten minutes after kick-off), fetches the summary, and stores a line-up only when both sides have eleven starters. Immutable files with `observed_at`, the raw response's SHA-256 and a line-up digest; an unchanged line-up is not stored again, a changed one before kick-off is a new file; a club the directory cannot resolve is skipped and counted (72 of 74 names resolved on the day of the probe; "Internazionale" got an exact alias). Folders older than three days are removed.
* **Where it flows:** collected in `shadow-cloud` into the `shadow-data` branch; `markets-cloud` copies `artifacts/lineups` into `markets-data`; the Vercel bundle carries the newest file per match; the PC sync includes it.
* **Page:** the "Eksikler" tab shows "Kadrolar (bilgi amaçlı)": both starting elevens ordered by formation place, formation, substitutes, source and observation time; before the announcement it says so plainly. English translation included.
* **It is never a feature.** No probability reads it; the shadow records still say `lineups: UNKNOWN` (the models have no line-up information and no history to calibrate a line-up effect on).

## 2. Readiness report
`python -m src.readiness [--github]` lists, per item, SET or NOT_SET (names only, never a value; the GitHub part reads repository secret NAMES through `gh secret list`), what it unlocks and what the owner does next. It changes nothing and calls nothing but `gh`.

## Limits
ESPN's terms are unverified and the feed can change or disappear; a line-up may be missing for a competition ESPN does not cover. The card cannot say who will actually play after a late change (the last stored file wins, with its time). The API-Football suspension is the owner's to resolve on their dashboard.
