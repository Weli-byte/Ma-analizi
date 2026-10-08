# ADR 0038 — openfootball/football.json as the first commercially usable free source

Status: accepted · 2026-10-08

- The repository declares its schema, data and scripts "dedicated to the public domain. Use as you please
  with no restrictions whatsoever" (read 2026-10-08). It is the only source here with an explicit
  commercial-use grant, and it needs no key and no payment.
- `src/ingestion/openfootball.py` ingests EPL and La Liga results/fixtures (daily-updated season files) into
  the existing results store (`ingest_finished(..., source="openfootball", id_prefix="of")`); football-data.org
  stays first when a key exists, openfootball fills gaps and is the fallback. De-duplicated by teams + day.
- Limits: scores and dates only (no lineups, injuries, odds, events); kick-off times are local clock times
  without a zone and are converted with the league zone (INFERRED, flagged); a bare `[h, a]` score shape
  appears for 0-0 results (cross-checked against football-data.co.uk); completeness depends on community
  contributions. Aliases are `fuzzy_approved` for the owner to re-confirm.
- Not a full answer: odds, injuries and lineups still have no free source with a commercial grant.
