# ADR 0079 - Cross-machine reproduction check of Sprint 4c and 4d

Date: 2026-10-25. Status: accepted.

## Context
Sprint 4c and 4d outputs were produced on the owner's Windows machine and reproduced there twice (byte-identical). Earlier sprints (2 to 5) are re-run on a different machine, GitHub's Ubuntu runner, by the `league-benchmark` workflow against the archived public-domain source snapshot, and compared with the committed files (exact for structure and text, within 1e-3 for numbers). Sprint 4c and 4d were not part of that.

## Decision
* `s4c_benchmark --compare-committed docs/league_models --atol 1e-3` and `s4d_experiment --compare-committed ...` re-run into a temporary directory and compare every committed output (`s4c_*.json`, `s4d_extension.json`) with `benchmark._walk`: numbers within the tolerance, text and structure exactly, so a different chosen candidate, a different verdict or a different decision is never float noise. The config fingerprints are ignored (they legitimately change when a later sprint adds a section to `s4c.yaml`; the sections a run uses are compared through its results). Exit code 1 on any difference.
* Both comparisons are steps of the `league-benchmark` workflow (they run on a pull request that touches the league-model code, configs or these result files, on pushes to main that touch the code, and on demand), on the archived snapshot, with single-threaded BLAS like the earlier sprints.
* The function itself is unit-tested on synthetic files; the real comparison is the workflow.

## Limits
Float noise between operating systems is bounded, but near-ties in a nested selection (two candidates equal to six digits) could flip between machines and then fail the exact-text rule; if that ever happens it is reported as a failure, not hidden, and the tie rule (earlier grid entry) is the place to look. LightGBM results are compared with the same tolerance as the other sprints (XGBoost is the one the earlier sprints already treat as backend-sensitive; Sprint 4c/4d use LightGBM only).

## First Ubuntu run (2026-10-25)
Everything agreed within 1e-3 (metrics, paired intervals, decisions, exact-score tables, feature audit, manifest, the candidates chosen by the nested selection) except one kind of value: `parameter_digest`, the SHA-256 of the fitted parameters rounded to 8 decimals, which differed in every entry. A hash cannot be compared within a tolerance and the last digits of an L-BFGS solution depend on the operating system, so the digests are excluded from the comparison (`HASH_KEYS`); the parameters are covered through the results they produce, and the chosen candidates still have to agree exactly.
