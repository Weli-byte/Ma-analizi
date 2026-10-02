# LLM historical benchmark: 20 validation matches x 3 real providers (2026-10-02)

Command: `ALLOW_REAL_LLM_CALLS=true python -m src.llm.benchmark --track historical --limit 20`
(60 real API requests, 60/60 successful, estimated cost $0.021, prompt `llm-prompt-v2`,
pricing 2026-10-01, data `dv-6f3af90c6bdb`, EPL + LaLiga 2022-23/2023-24, evenly spaced sample).

READ THIS BEFORE USING THE NUMBERS
- **Memorization risk (HISTORICAL track).** Accuracy of 70-85% on 1X2 football is far above what
  any forecaster achieves on unseen matches (about 50-55%). These matches happened before the
  models' training cutoff; the LLMs most likely know the results. This benchmark measures
  plumbing and calibration mechanics, NOT genuine forecasting skill. Only the PROSPECTIVE track
  (forecasts made before kickoff, scored after the match) can measure skill.
- n = 20 per model; temperature calibration is fit on 10 matches and reported on the other 10.
  The intervals are wide; differences between models are not established. No winner is declared.
- Calibration helped OpenAI and Gemini (lower Brier/RPS/ECE) and hurt Groq (Brier slightly
  lower, log loss/RPS/ECE higher): with 10 fitting rows that is noise, not a finding.
- The first attempt (same fixtures) had Groq at 40% coverage because the 429 retry waited only
  1-2 s; `retry-after` is now honoured (ADR 0024 addendum) and the rerun reached 100% coverage.
