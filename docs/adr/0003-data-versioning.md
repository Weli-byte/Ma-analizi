# ADR 0003: Content-derived data versions and atomic pipeline

## Status
Accepted — 2026-09-26.

## Context
`dv1` was a static label: changing a raw file kept the same version, and artifacts (features, reports)
did not record which data they came from. The pipeline deleted the current dataset before rebuilding.

## Decision
- `data_version = "dv-" + sha256(canonical({sorted (league, season, raw sha256), normalization inputs hash}))[:12]`.
  Deviation from "raw hashes only": the normalization-inputs hash (league formats, alias store, parser
  version, table schema version) is included, because a parser or alias change alters the normalized
  data without touching raw bytes; a version that ignores it would be a lie.
- The version is embedded in: dataset meta, feature lineage, `FeatureSnapshot`, `PredictionRecord`,
  `ExperimentRecord`, run reports. Loaders compare versions AND the dataset content hash and fail loudly
  (`StaleArtifactError`).
- Pipeline stages: download → checksum → parse → validate → quality → write → commit. All output goes to
  `data/processed/.tmp-*`; commit = `os.replace(tmp, data/processed/<dv>)` then atomic rewrite of
  `CURRENT.json`. A failure or crash leaves the previous dataset and pointer untouched; stale `.tmp-*`
  directories are removed at the next run.
- Rebuilding an existing version must reproduce the same content hash, otherwise the run fails
  (bump `PARSER_VERSION` / `SCHEMA_VERSION` when logic changes).

## Alternatives
Sequential integers (rejected: not tamper-evident); git-SHA-based versions (rejected: unrelated to data).

## Consequences
Approving a team alias changes the data version (it changes normalization inputs). Old dataset
directories accumulate; a retention policy is future work.
