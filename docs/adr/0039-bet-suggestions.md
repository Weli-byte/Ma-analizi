# ADR 0039 — Bet suggestions (owner decision, 2026-10-08)

Status: accepted · amends ADR 0030 ("paper only") and the CLAUDE.md rule "value analytics (paper only)"

The owner asked for bet suggestions. The system now OUTPUTS suggestions (`GET /v1/value-picks`, dashboard
section "Bet suggestions"); it still never places a bet, and the paper ledger stays for honest evaluation.

Rules kept: a suggestion needs a complete 1X2 set of `exact`-timestamp quotes observed after the forecast and
before kickoff (ADR 0030), edge >= `min_edge` AND EV >= `min_ev` (`configs/odds.yaml`). Several models voting for
the same selection are aggregated on the mean probability. Stake hint = quarter Kelly (`kelly_fraction`),
capped at `max_stake_pct` of bankroll. Confidence is LOW or MEDIUM, never higher: no model has been shown to
beat the market out of sample (the historical LLM benchmark has n=20 per model and possible memorization).
Every pick carries caveats (not a guarantee, odds may move, 18+, help resource). Outputs never use "sure",
"guaranteed" or similar wording.
