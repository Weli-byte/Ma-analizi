# Real-data CI sanity layer (S0-S7 hardening, Phase 3)

Two CI layers now exist for regression safety, closing hardening audit finding H-02/H-04:

## 1. PR-time: `real-data-sanity` job (`.github/workflows/ci.yml`)

Runs `scripts/ci_real_data_sanity.py --mode strict` on every push/PR. Uses
`tests/fixtures/real_smoke/root`: three genuinely real, unmodified EPL seasons
(2021-22..2023-24, 1140 matches, football-data.co.uk, committed to the repo) — real data, but
small and network-free, so it stays fast (~15s locally for two full runs) and deterministic
enough to gate every PR.

Distinct from `tests/fixtures/golden` (36 SYNTHETIC fixtures, fabricated team names) — the real
fixture exists specifically to catch what only shows up on real, non-hand-crafted data: real
team-name variety (team resolution against the FULL `configs/team_aliases.yaml`), real
season-boundary quirks, real null patterns in optional columns, real odds ranges.

What it checks, twice (once per run, then diffs the two runs for determinism):
- **Ingestion/normalization**: exact raw/accepted row counts (1140/1140, 0 rejected), all three
  seasons reach `historical_complete` status.
- **Feature generation**: `build_features` succeeds — this ALREADY runs
  `src.features.leakage_audit.audit_leakage` internally and raises on any violation, so a
  failure here IS a leakage-audit failure on real data, not a separate bolted-on check.
- **Split generation**: `build_split_manifest`'s own chronological-ordering assertion.
- **Every configured model** (`always_home`, `historical_prior`, `recent_form_naive`,
  `market_implied`, `elo`, `poisson`, `dixon_coles`, `xgboost`, `lightgbm`): finite metrics,
  valid probability distributions (`metrics.validate`), and (this doubles as GBM
  loading/training smoke and Elo/Poisson/Dixon-Coles smoke, per the hardening plan) a normal
  `run_baselines` pass.
- **Walk-forward smoke**: at least one fold executes with the full model set.
- **Determinism**: the ENTIRE chain (pipeline → features → baselines → walk-forward) runs twice
  from a clean state; every content hash (predictions, report, metrics — for both
  `run_baselines` and `walk_forward`) must be byte-identical between the two runs. This is the
  strongest evidence yet that the pipeline, every S4-S6 model (including XGBoost/LightGBM with
  Optuna), and the walk-forward engine are ALL deterministic at real-data scale, not just on the
  36-fixture golden project (audit finding L-04 is partially addressed by this — still same-
  machine only, not cross-platform).

## 2. Scheduled: `nightly-full-benchmark` job (`.github/workflows/nightly-full-benchmark.yml`)

Cron (03:17 UTC daily) + manual `workflow_dispatch`. Downloads the FULL real dataset (both
leagues, every configured season, `python -m src.data.download`, primary football-data.co.uk +
Wayback fallback per `configs/sources.yaml`) and runs the full `research`-mode pipeline →
features → baselines → walk-forward chain, uploading `artifacts/runs/` and
`artifacts/walk_forward/` as a 30-day-retained CI artifact.

`research` mode, not `strict`: the current-partial season's source file changes daily upstream,
so a pinned-checksum drift there must not fail this scheduled job (provenance/quality
enforcement still fully applies in research mode — only the checksum-pinning requirement is
strict-only).

## What's still open (tracked in the hardening audit)

- **Weekly extended regression benchmark** (explicitly requested by the S0-S7 hardening task,
  separate from the nightly full benchmark): not yet built. What "extended" should mean beyond
  the nightly run (larger date range? additional leagues? a stricter regression-threshold
  comparison against a stored baseline?) needs a decision before building it — tracked as a
  remaining Phase 3 item, not silently dropped.
- ~~The real-data reproducibility TEST~~ — done: `tests/test_real_data_reproducibility.py`
  (Phase 5, audit finding H-04, CLOSED). Reuses `ci_real_data_sanity.run_sanity` rather than
  duplicating its two-runs-and-diff logic; kept deliberately same-process so it can't hit
  H-09's cross-CI-runner CPU/SIMD-dispatch variance.
