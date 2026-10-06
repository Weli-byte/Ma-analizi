# ADR 0026 — LLM benchmark engine, LLM_REAL class, coverage, ensemble integration

Status: accepted · 2026-10-02 · extends ADR 0019/0020/0022/0024

## Decision
- **One engine:** `python -m src.llm.benchmark` (`src/llm/benchmark.py`). `src.llm.cli` is a thin
  alias for the historical track. It prints the plan first (MATCH/REQUEST/PROVIDER/MODEL COUNT,
  ESTIMATED MAX COST, ESTIMATED TOKEN BUDGET), then needs `ALLOW_REAL_LLM_CALLS=true`; without it
  the result is `REAL_CALLS_DISABLED_BY_OPERATOR` (exit 4): no call, benchmark NOT complete,
  never a mock. The configured budget is checked for the whole run before the first call; CLI
  flags (`--max-requests`, `--max-cost`, `--concurrency`) can only lower it.
- **Tracks:** `historical` = chronological VALIDATION (or train) split, deterministic evenly
  spaced sample, memorization-risk caveat in every summary; final-test is rejected. `prospective`
  = real upcoming fixtures (football-data.org), cutoff = now, refused at/after kickoff before any
  call. `--prompt-version` must equal the production `PROMPT_VERSION`.
- **Failure policy:** a failed request yields no prediction and is counted; there is no fallback
  to another provider/model/older prediction. Keyless providers are `NOT_CONFIGURED`.
- **Coverage** (`src/llm/coverage.py`): by provider, model, league, season, stage (`T-<h>h`),
  success/failed/coverage/failure rate; failures are never dropped.
- **`LLM_REAL` model class** (`src/llm/evaluation.py`): same metrics, bootstrap CIs, by-league /
  by-season and S9 temperature calibration as every other model (raw and calibrated reported side
  by side, calibration fit on the chronological first half, reported on the second; per
  provider/model, never pooled). Predictions without complete provenance are not evaluated
  (`audit_provenance`); fixtures without a known outcome are `awaiting_result`.
- **Ensemble:** `python -m src.evaluation.run_ensemble --llm-run <llm_runs/dir> ...` adds REAL LLM
  base models (only successful records exist in `predictions.jsonl`). A model covering fewer than
  `MIN_LLM_FIXTURES`=20 OOF fixtures is excluded and the coverage is reported; a stale
  `data_version` fails loudly. Output goes to a separate `..._ensemble_llm` directory; existing
  non-LLM ensemble artifacts are untouched.
- Artifacts per run: plan, predictions, calls, raw responses, coverage, evaluation, outcomes
  (post-hoc scoring labels, never sent to a provider), summary, hashes.

## Evidence (2026-10-02, real calls)
3 providers x 2 validation fixtures (6 real predictions, ~$0.0021) plus 1 prospective fixture x 3
providers (~$0.0008). Real outputs are stored as test fixtures under
`tests/fixtures/real_provider_captures/benchmark_historical/`.

## Limits (honest)
2 fixtures per model is a plumbing check, not a result: calibration is correctly reported as
skipped. A meaningful benchmark (>= 20 fixtures per model, ~60 requests, order of $0.03) needs the
owner to raise `budget.max_requests_per_run` / `max_total_tokens` deliberately. Walk-forward OOF
artifacts were not generated here, so the LLM ensemble has been verified only on its integration
logic, not on a full run.
