# Dependency Security Audit (S0-S7 Hardening Phase 24, M-12)

**Date:** 2026-09-30
**Tool:** `pip-audit` 2.10.1 (PyPI Advisory Database + OSV)
**Scope:** `requirements.lock` (full hashed transitive closure, including xgboost, lightgbm,
optuna, shap, and their transitive dependencies — numba, llvmlite, scipy, pandas, scikit-learn,
sqlalchemy, alembic, and the rest of the S6 dependency surface).

## Result

```
$ python -m pip_audit -r requirements.lock
No known vulnerabilities found
```

No known CVEs in the current locked dependency set as of this audit date.

## Process

Run `python -m pip_audit -r requirements.lock` after every `requirements.lock` regeneration
(see `CLAUDE.md` lock-regeneration policy, Phase 23 / H-05). `pip-audit` itself is a one-off dev
tool, not added to `pyproject.toml` — it queries the lock file directly and needs no project
dependency.

## Limitations

- Vulnerability databases lag disclosure; a clean run is a point-in-time result, not a permanent
  guarantee — re-run on every dependency bump, and periodically even without one.
- License auditing is out of scope for this pass (M-12 asked for "license/CVE audit" — CVE side
  done here; license compliance is a separate, not-yet-scoped task since this is a RESEARCH_ONLY
  project with no distribution currently planned, per `docs/data_sources/licensing.md`).

## Status

M-12: **CLOSED (Phase 24)** — CVE audit run and recorded; license audit explicitly deferred
(no distribution scope yet).
