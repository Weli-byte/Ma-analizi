# Versioning, Traceability and Git Standards

## Identifiers
| Item | Format | Example |
|---|---|---|
| model_id | `^[a-z][a-z0-9_]*$` | `elo`, `dixon_coles` |
| model_version | semver | `1.0.0` |
| feature_version | `fvN` (bump on ANY feature logic/registry change) | `fv2` |
| data_version | **content-derived** `dv-<12 hex>` (see ADR 0003) | `dv-3fa9c2b1d07e` |
| builder_version | `builder-X.Y.Z` | `builder-2.0.0` |
| parser_version | `parser-X.Y.Z` | `parser-2.0.0` |

## Git
- **Branches:** `main` = always green, only fast-forward/PR merges. Work happens on `sprint/sN-name`,
  `remediation/<scope>`, `fix/<topic>`. Never commit directly to `main` once branch protection exists.
- **Commits:** Conventional Commits, one commit per completed phase/sprint:
  `<type>(<scope>): <summary>` with types `feat fix test docs chore refactor perf ci`, scope = sprint
  or subsystem (`s3`, `data`, `features`, `ci`). Body explains WHY. Co-author trailer when AI-assisted.
- **Tags:** `sN-complete` after each sprint is green in CI; `data-<data_version>` when a dataset
  version is published; `vX.Y.Z` for releases (semver, first release after S19).
- **Releases:** a release tag requires: green CI on all matrix versions, reproducibility test pass,
  final remediation/release report, no `unknown` provenance.

## Experiment traceability
Every experiment record stores: `experiment_id, git_sha, git_dirty (+ dirty files), python_version,
platform, dependency_lock_hash, data_version, feature_version, config_hash, model_name, model_version,
seed, split_id, train/validation/final_test rows, metrics, created_at_utc, run_mode`.
- SHA `unknown` is only legal in DEVELOPMENT mode.
- Dirty tree: RESEARCH records `dirty=true` + file list; STRICT/FINAL refuse to run.
- Run outputs go to gitignored `artifacts/`, so producing an experiment never dirties the tree.

## Dependencies (see ADR 0011)
- Canonical source: `pyproject.toml`. Lock: `requirements.lock` (uv universal, hashed).
- Supported Python: **3.12 and 3.14** (CI matrix). OS: Linux (CI), Windows 11 (developer machine).
- Install: `pip install --require-hashes -r requirements.lock && pip install --no-deps -e .`
- Regenerate: `uv pip compile pyproject.toml --extra dev --universal --generate-hashes --python-version 3.12 -o requirements.lock`
  (CI job `lock-up-to-date` fails if the lock drifts from `pyproject.toml`).
