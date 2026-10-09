# ADR 0043 — Do not redistribute raw odds (The Odds API terms)

Status: accepted · 2026-10-09

The Odds API terms (read 2026-10-09) allow commercial use, display, storage, derived values and ML training, and
forbid serving its data as a raw feed (own API, downloadable files).
- The public API no longer returns raw bookmaker prices: fixtures show de-vigged implied probabilities per
  source; `/v1/value-research` drops the price triple; `/v1/value-picks` keeps the single price of a suggestion
  (the price is the point of the suggestion).
- ESPN prices (no grant) are never served by the API.
- Responsible-gambling text ("Gamble Responsibly. 18+") is shown in the dashboard footer; suggestions already carry
  an 18+ caveat.
- OPEN, owner action: the `odds-data` branch holds raw quotes in a public repository. Fix: create a PRIVATE repo
  for data, add a fine-grained token as secret `DATA_REPO_TOKEN`, and point `odds-exact.yml` /
  `markets-cloud.yml` at it (Actions minutes of the public repo stay free). Until then the repository should not be
  treated as launched.
