# ADR 0042 — Cloud generation of match intelligence

Status: accepted · 2026-10-09

`.github/workflows/markets-cloud.yml` (every 4 hours + manual) downloads the official CSVs from GitHub's network
(the owner's ISP blocks football-data.co.uk), rebuilds the dataset in research mode, ingests recent results
(football-data.org + openfootball), syncs exact odds from `odds-data`, writes the immutable intelligence
artifacts and commits them to the `markets-data` branch. The PC (or any host) pulls with
`python -m src.markets.run --sync-remote`; a normal local run also syncs first (add-only, immutable files never
replaced). This removes the "PC must be on" dependency for match intelligence. The S13 stage locks (10-minute
windows) and S14 live ticks are NOT moved: they need tighter timing and are still PC-driven (open item).
