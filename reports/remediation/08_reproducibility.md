# 08 — Reproducibility

**Real data (executed):** strict-mode chain run, then all generated artifacts deleted (`data/processed`,
`data/features`, `artifacts`), then the chain re-run:

| item | run 1 = run 2 |
|---|---|
| data_version | `dv-6f3af90c6bdb` ✔ |
| normalized dataset content hash | `dd4dba08…f907` ✔ |
| feature content hash (5350 rows) | `a925c80a…7a7d` ✔ |
| predictions hash | `b76e6677…4c65` ✔ |
| metrics hash | `8249b3e8…1e2c` ✔ |
| report hash | `b1dcca97…2e18` ✔ |

**REPRODUCIBLE = true.** Timestamps (experiment `created_at_utc`, lineage `created_at_utc`) are excluded from the compared
hashes; they live in `experiments/*.json` and `feature_lineage.json` only.

**Golden fixture (tests):** identical outputs after deleting artifacts, across two independent project copies (different
paths/SHAs), and across `strict` vs `research` modes. Golden files are compared against committed values.
Cross-OS: CI (Linux) passes the same golden hashes computed on Windows.
