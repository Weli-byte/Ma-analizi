# 01 — Repository, Git, CI, dependencies

- Branch: `remediation/s0-s3` (audited commit `8580908`); `main` still at `c7dc5dd` (not merged).
- Commit history (real, conventional): `810c3c9` audit register → `13ca3e3` CI/deps/provenance → `8580908`
  remediation phases C–K.
- **CI (actual GitHub Actions run 36122384759 on `8580908`): success** — jobs `test (3.12)`, `test (3.14)`,
  `lock-up-to-date`; steps incl. hash-verified install, import smoke test, ruff, pytest with coverage and the
  critical-path coverage gate. No step was skipped or failed.
- Dependencies: `pyproject.toml` is the single source; `requirements.lock` is a hashed universal `uv` lock (459+ hashes).
  Supported Python 3.12 and 3.14; 3.13 untested. OS: Linux (CI), Windows 11 (developer).
- Clean-environment install: performed by CI on fresh runners (both Python versions). A local clean-venv attempt
  was killed by Claude Code's low-memory guard and was NOT repeated; no local clean-install claim is made.
- Experiments record a real 40-hex SHA; `unknown` is only legal in DEVELOPMENT (schema-enforced); STRICT/FINAL refuse a
  dirty tree; RESEARCH records `dirty=true` + file list.
