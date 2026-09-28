# S0–S3 Remediation Audit

Source: "Sprint 0-3 Eksiklik ve Hata Raporu" (2026-09-25). Baseline commit audited: `c7dc5dd` on `main`.
Status column is updated at the end of the remediation (see `reports/remediation/FINAL_S0_S3_REMEDIATION_REPORT.md`).

## Environment facts (measured, not assumed)
- Python 3.14.6 locally, CI 3.12. Package manager: pip + venv (uv available). Test runner: pytest 9. Lint: ruff.
  Type checking: not configured.
- CI run on `c7dc5dd` (actual GitHub Actions, Python 3.12): **success** — but with all real-data tests skipped
  because raw data is absent in CI.
- Raw data: 10 CSV files in `data/raw/football_data/` (gitignored). Processed `data/processed/dv1/`, features
  `data/features/fv1/`, reports `reports/` (baseline outputs).

## Dependency graph
```
raw CSV (data/raw/football_data)      manifest.json (sha256, origin?)   team_aliases.yaml
        |                                    |                               |
        v                                    v                               v
   clean_row (src/data/clean.py) --------- pipeline (src/data/pipeline.py) -- TeamRegistry
        |                                          |
        v                                          v
 canonical fixtures/results/stats/odds  ->  data/processed/dv1/{football.duckdb, *.parquet}
        |                                          |
        v                                          v
 load_matches (features/builder.py) -> MatchHistory -> compute_features -> FeatureSnapshot
        |                                          |
        v                                          v
 data/features/fv1/{features.parquet, feature_lineage.json}      quality report
        |
        v
 load_rows (evaluation/dataset.py: fixtures+results+features+odds) -> EvalRow
        |
        v
 baselines (models/baselines.py) -> runner.evaluate -> metrics.py -> reports/baselines_dv1_fv1.{md,json}
```

## Findings

| ID | Sev | Current behavior | Root cause | Affected files | Risk | Required fix | Test required | Status |
|---|---|---|---|---|---|---|---|---|
| F1 | HIGH | Single commit; git_sha `unknown` in experiments; CI ran once (3.12 only, data tests skipped) | No provenance module; CI matrix single | ci.yml, run_baselines.py | Untraceable experiments | Real SHA/dirty capture, strict modes, CI matrix + fixture data | provenance + CI | fixed (commit history, real SHA in experiments, CI green on 3.12+3.14: run 36122384759) |
| F2 | HIGH | Data ends 2024-05-26, 2 leagues | Only 2019-24 downloaded | data/raw | Stale benchmark | Add 2024-25, 2025-26, partial 2026-27 with categories | season-status tests | fixed (2024-25, 2025-26 complete; 2026-27 CURRENT_PARTIAL) — archive-origin, see F3 |
| F3 | HIGH | manifest labels archive copies `football-data.co.uk`; retrieval_time = registration time; no expected checksums | manifest.py minimal | manifest.py | Provenance lies | Provenance sidecars, origin, expected_checksums.json, raw validation | manifest + validate tests | mitigated: origin/URL/time recorded, checksums pinned; NOT verified_official (site TLS unavailable) |
| F4 | HIGH | `dv1` static label; artifacts lack data_version | version not content-derived | pipeline.py, builder.py, schemas | Silent stale data | Content-derived `dv-<hash>`; embed everywhere; loader verifies | stale artifact tests | fixed (dv-<hash>, embedded, stale artifacts fail) |
| F5 | HIGH | Missing features silently fall back to prior | no availability report | dataset.py, baselines.py | Hidden degradation | FeatureAvailabilityReport + STRICT/RESEARCH modes | fallback tests | fixed (availability report + mode thresholds) |
| F6 | HIGH | Final test protected by config text only | `load_rows` reads any season | dataset.py, config | Final-test contamination | EvaluationContext + guarded loader + final path | guard tests | fixed (EvaluationContext lock, split manifest, bootstrap CI) |
| F7 | HIGH | odds untimestamped; Avg/Max treated as bookmakers; closing used as baseline | schema lacks semantics | clean.py, baselines.py | Misuse in S15 | snapshot_type, market_source_type, timestamp_quality, REFERENCE class | odds tests | mitigated (explicit semantics; exact-timestamp odds still unavailable -> S12) |
| F8 | MED | rest_days unbounded (max 811d) | no cap/flag | compute.py | Outlier features | raw/capped/season_break | rest tests | fixed (raw/capped/flag) |
| F9 | MED | NaN reasons indistinguishable | only avail flag | compute.py | Bad imputation | reason codes + availability indicators + NaN policy doc | reason tests | fixed (reason codes + availability flags + policy) |
| F10 | MED | 4 xG columns 100% NaN active | xG absent in source | registry.py, compute.py | Junk inputs | remove from active set; docs/data_sources/xg.md | registry test | mitigated (xG experimental, not produced; no source yet) |
| F11 | MED | Dead/mismatched config fields | config not consumed | config, yaml | False confidence | connect or remove; consumption test | config-consumption test | fixed (consumption test; provider fields reserved for S8) |
| F12 | MED | frozen models hold mutable dicts; prediction_id ignores probabilities | shallow immutability | schemas | Silent mutation | deep freeze; content-hash id; ledger conflict check | immutability tests | fixed (deep freeze, content-hash ids, ledger, state machine) |
| F13 | MED | UK-time assumption unverified; naive timestamps in DuckDB | no tz module/tests | clean.py, pipeline.py | Cutoff drift | tz module, DST tests, TIMESTAMPTZ | DST tests | fixed (tz module + DST tests); UK-time assumption for Spain unverified beyond samples |
| F14 | MED | RESULT_LAG constant; no lifecycle | simplistic model | history.py, fixture.py | Wrong availability | lifecycle statuses + result_available_at (+source) | lifecycle tests | mitigated (lifecycle + inferred availability labelled); observed times need S12 |
| F15 | MED | Alias table hand-written; new names auto-registered | no identity system | teams.py | Phantom teams at scale | alias store w/ validity + review CLI | team resolution tests | fixed (identity store + CLI); 7 alias approvals need owner review |
| F16 | MED | Narrow quality checks | quality.py minimal | quality.py | Silent corruption | 20-check engine, LeagueFormatConfig | quality tests | fixed (20-check engine, LeagueFormat) |
| F17 | MED | rmtree-then-rebuild; no adapter | in-place build | pipeline.py | Data loss on failure | atomic build + pointer + failure injection | atomicity tests | fixed (atomic pipeline + failure injection); adapter interface for a 2nd source deferred to S12 |
| F18 | MED | ECE/CI missing; accuracy tie -> home | not implemented | metrics.py | Misread reports | ECE, bootstrap CI, explicit tie rule | metric tests | fixed (ECE, bootstrap CI, tie rule); ROI/CLV intentionally not implemented |
| F19 | MED | main() paths, download script untested; data tests skipped in CI; no golden/regression/coverage | no fixtures | tests, CI | False green | golden dataset, CLI tests, coverage | golden/regression | fixed (golden, regression, CLI tests, coverage gate 97.7%) |
| F20 | MED | pip-freeze lock without hashes; two dep sources | ad-hoc | pyproject, requirements.* | Irreproducible env | pyproject canonical + hashed universal lock | clean-venv install | fixed (pyproject + hashed uv lock; py3.12/3.14 in CI) |
| F21 | LOW | production `assert`; cp1254 console crash; runpy warning | hygiene | dataset.py, fixture.py, mains | Odd failures | ValueError, UTF-8 output, lazy imports | lint/rule test | fixed (no asserts in src, UTF-8 output, no runpy warnings) |
| F22 | LOW | 1 ADR only | docs | docs/adr | Lost rationale | ADR 0002–0012 | doc presence | fixed (ADR 0002-0012) |
| F23 | LOW | `--insecure` download flag | convenience | scripts/download_football_data.py | MITM risk | remove; verified TLS + declared fallback | downloader tests | fixed (insecure script removed, verified TLS, declared fallback) |
| P1 | PROC | `taskkill /IM python.exe` killed all python | careless | (process) | Data loss | never; PID-owned only | rule + test (no taskkill in code) | policy adopted |
| P2–P11 | PROC | Unapproved choices (Wayback, closing odds, split) | (process) | — | — | Document in ADRs; ask for licensing/data decisions | n/a | documented in ADR 0012/0007 |
