# 06 — Tests and coverage

- **319 tests, all passing** locally (Windows, Python 3.14, `pytest --cov`); CI ran the same suite on Ubuntu with Python
  3.12 and 3.14 (success).
- Coverage: overall **97.7%**; critical-path groups (gate ≥ 90%): data ingestion 97.1, normalization 96.2, feature
  generation 97.2, split logic 94.9, leakage guard 100, evaluation 98.8, prediction id/lifecycle 99.1,
  lineage/provenance 99.3, quality checks 98.9. `scripts/check_critical_coverage.py` enforces it in CI.
- Layers: unit, integration (pipeline → features → baselines on a committed synthetic fixture), regression (golden
  files), replay/reproducibility, failure injection, CLI entrypoints (every `main()`), hygiene (no `assert` in src, no
  TLS bypass, no blanket process kills, no `or True` in tests, config-consumption test, ADR sections).
- CI does not depend on the real dataset: real-data tests are not needed for the meaningful checks; the golden DEMO league
  (`tests/fixtures/golden`, synthetic test data, clearly labelled) exercises everything.
- Tests changed on purpose (all documented): accuracy tie rule (ADR 0012), `data_version` format (`dv-…`), odds column
  semantics (ADR 0007), fv2 feature names. No expectation was weakened to obtain green; bugs found by the new tests
  were fixed in code (ledger JSON reload, Q18 for partial seasons, Q11/Q12 for incomplete seasons).
- Not configured: static type checking (mypy).
