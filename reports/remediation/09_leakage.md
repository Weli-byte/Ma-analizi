# 09 — Leakage

- Real-data audit (builder, strict): 300 random (fixture, cutoff) samples, seed 42 → **0 violations**.
- The audit scrambles/deletes every result, post-match statistic and closing odd unavailable at the cutoff (and the
  fixture's own result); features and their `available_at`/reasons must be identical.
- Proof that the audit works: it catches all of these intentionally broken implementations (tests): current-fixture result,
  future goals, future fixtures count, future rolling window, future opponent strength, future standings, post-match
  statistics, closing odds unavailable at cutoff, results published after the cutoff, and a cutoff-ignoring function.
- Postponed/cancelled/abandoned/scheduled/rescheduled/in-progress matches never enter history, even when they carry scores.
- Final-test access: rejected in TRAIN, VALIDATION, TUNING, CALIBRATION, MODEL_SELECTION and ENSEMBLE_FIT contexts; the baseline
  run is proven (spy test) to request only train/validation seasons; seasons outside every split are unreadable even in FINAL.
- Residual risk: `result_available_at_utc` is inferred (kickoff + 3h); an overrunning match could be marked available
  slightly early. Observed availability times require a live source (S12).
