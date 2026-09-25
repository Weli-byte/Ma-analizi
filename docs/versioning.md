# Versioning, Naming, Git Standards

| Item | Format | Example |
|---|---|---|
| model_id | `^[a-z][a-z0-9_]*$` | `elo`, `dixon_coles`, `llm_claude` |
| model_version | semver | `1.0.0` |
| feature_version | `fvN` | `fv1` |
| data_version | `dvN` | `dv1` |
| run_id | free, unique | `2026-09-25-s3-baselines` |

Bump feature_version when any feature definition changes; data_version when the processed dataset changes.

## Git
- Branches: `main` (protected), `sprint/sN-short-name`, `fix/...`.
- PR per sprint task; CI (ruff + import check + pytest) must be green.
- Commits: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), scope = sprint e.g. `feat(s1): ...`.
- Issues: template `.github/ISSUE_TEMPLATE/task.md`, labeled `sN`.
