# 03 — Lineage

- Chain: raw sha256 → manifest → `data_version` → dataset meta (content hash, table hashes, raw files, parser/schema
  versions, timezone conversion version, as_of) → feature lineage (data_version, dataset content hash, feature_version,
  builder_version, registry hash, source files/checksums, git SHA/dirty, config hash, python/lock hash, parquet sha256,
  content hash) → `FeatureSnapshot.data_version` → `PredictionRecord.data_version` → `ExperimentRecord`.
- Loading a feature artifact verifies data_version, dataset content hash, feature_version, registry hash, parquet
  checksum and row count. Tested failure modes: data version mismatch, dataset content mismatch, wrong feature
  version, registry changed without a version bump, corrupted/edited parquet, wrong row count, dataset re-versioned
  without rebuilding features (`StaleArtifactError`).
- Prediction identity: `logical_id` (fixture, model, versions, cutoff), `content_hash` (adds kickoff, generated_at,
  probabilities), `prediction_id = content_hash[:16]`. Ledger rejects same logical id with different content;
  JSONL reload verifies stored ids (tamper detection). Status machine DRAFT→PUBLISHED→LOCKED→EVALUATED (+VOID);
  all 25 status pairs tested.
- Every experiment record stores: experiment_id, git_sha, git_dirty (+files), python_version, platform,
  dependency_lock_hash, data_version, feature_version, config_hash, model_name/version, seed, split_id,
  train/validation/final_test rows, metrics, created_at_utc, run_mode.
