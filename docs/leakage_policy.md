# Leakage Policy

1. Any info learned after the prediction timestamp (`information_cutoff`) is banned from features.
2. Rolling features exclude the current fixture (window ends at t-1).
3. Every feature declares `source, window, aggregation, available_at, leakage_rule`
   (`FeatureSpec`). Example: `form_points_5 | results | last 5 | points sum | t-1 | current match excluded`.
4. `FeatureSnapshot` rejects `available_at > information_cutoff` and `cutoff > kickoff`.
5. Insufficient history = `None`/NaN + availability flag. Never silently 0.
6. Result availability: only FINISHED matches with `result_available_at_utc <= information_cutoff` are
   history. For historical data that time is INFERRED (kickoff + lag), labelled as such (ADR 0006).
7. Odds without an exact timestamp (all football-data odds) are never time-aligned signals (ADR 0007).
8. Team ratings (Elo) updated only after result; predictions use pre-match rating.
9. Final test set never used for selection/tuning. Ensemble stacking only on OOF.
10. Post-kickoff data never enters a pre-match snapshot.
11. Leakage audit (S2 harness, S10 audit command) is a release gate.
