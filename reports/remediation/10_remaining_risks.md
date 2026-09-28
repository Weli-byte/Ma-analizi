# 10 — Remaining risks and limitations

Scientific / data
1. **Licensing (owner action):** football-data.co.uk has no explicit reuse terms found → `RESEARCH_ONLY`; commercial or
   model-training use is not cleared. A licensed source is needed before S19.
2. **Provenance strength:** 13 of 16 raw files are Wayback copies; all checksums are `pinned_observed`, none
   `verified_official`. Confirm against the provider once its TLS works, then upgrade the status.
3. **Alias approvals:** 7 promoted-team aliases were approved by the assistant; the owner should review them.
4. **Odds:** no timestamps → no ROI/CLV/edge research possible; `market_implied` stays a reference baseline.
5. **xG:** unavailable; the feature family is experimental only.
6. **Result availability** is inferred (ADR 0006); kickoff times assume UK local time for both leagues (checked on samples,
   not proven for every match).
7. **Statistics:** validation has 2 seasons (1520 matches); baseline differences are small — no superiority claims.
8. **Final test** (2024-25, 2025-26) has never been evaluated; it can be evaluated exactly once per configuration.
9. Only 2 leagues; global scale-out, source adapters and observed timestamps belong to S12.

Engineering
10. CI covers Linux, Python 3.12/3.14 only; no static type checking; no local clean-venv install was completed (killed by a
    memory guard; CI's fresh runners cover the hash-verified install).
11. The lock is universal but was generated with Python 3.12 semantics; Python 3.13 untested.
12. `PredictionLedger` persists to JSONL only; a database-backed append-only ledger is planned for S7.
13. Dataset directories accumulate under `data/processed/` (no retention policy yet).
14. The final-test lock guards against accidents, not deliberate bypass (ADR 0004).
