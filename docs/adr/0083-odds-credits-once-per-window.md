# ADR 0083 — The Odds API credits: one call per window (2026-10-27)

## Finding
On 2026-10-26 both The Odds API keys were at 500 of 500 credits (free plan) and every cloud round logged "SKIPPED, every key is below the reserve of 25 credits".
The cause is the call pattern: the cloud loop collects every 10 minutes, and for the leagues with a football-data.org schedule (PL, PD, BL1, SA, FL1) a call was made in every round while any fixture was inside one of its collection windows
(`src/odds/schedule.py`: 60, 30, 25 and 12 minutes wide). One window therefore cost up to six calls, although a single call returns the odds of the whole league.

## Decision
- A window (kickoff | window index) that a call already answered is remembered per league in `artifacts/odds/_served_windows_<league>.json` (committed with the odds data, entries older than three days are dropped).
  A round whose open windows are all served spends no credit ("every open window was already collected"). A failed call does not mark anything, so it is retried.
- `--force` is unchanged and still calls. Cups and the Turkish league keep their interval rule. The credit reserve, the quota pause and the cup minimum are unchanged.
- The four windows per kickoff stay: the stages that feed the exact-odds gate (ADR 0030) lose nothing, only the repeats inside a window are dropped.

## Not changed
No model, no stored odds, no gate. Estimated effect: at most one call per league and window instead of one per round.
