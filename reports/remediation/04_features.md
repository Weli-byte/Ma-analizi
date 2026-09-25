# 04 — Features (fv2)

- Feature version `fv2` (bumped from `fv1`): rest days split into `rest_days_raw`, `rest_days_capped` (cap 30, config)
  and `season_break_flag`; xG features moved to `experimental` and are **not produced** (they were 100% NaN).
- 5350 snapshots on the real dataset. Leakage audit inside the builder: 300 samples, seed 42, **0 violations**.
- Unavailable values: `None` + `<name>_available=0` + reason (`dataset_start`, `new_team`, `insufficient_history`,
  `source_missing`). No silent zero.
- Only FINISHED matches with `result_available_at_utc <= information_cutoff` enter history; postponed/cancelled/
  abandoned/scheduled/rescheduled/in-progress matches are ignored (tested).
- Availability report enforced per run mode (validation run: 25/1520 fallbacks = 1.6%, all declared:
  `new_team` 5, `insufficient_history` 20; 0 unexpected).
- NaN policy for models: docs/adr/0009-missing-feature-policy.md.
- xG: docs/data_sources/xg.md — no validated source; nothing imputed.
