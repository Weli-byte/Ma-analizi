# Data source licensing register

**Rule:** a free, publicly downloadable dataset is NOT automatically licensed for commercial
redistribution, automated access, or AI/model training. Sources whose terms do not clearly permit these
are classified `RESEARCH_ONLY`, and no startup/production architecture may depend on redistributing them.
Research data sources and commercial production data sources are kept separate.

Last reviewed: 2026-10-01 (by the assistant; **not legal advice — the project owner must verify before any
commercial use**).

| Field | football-data.co.uk (CSV downloads) | Internet Archive / Wayback Machine (copies of the above) |
|---|---|---|
| provider | Football-Data.co.uk | Internet Archive |
| URL | https://www.football-data.co.uk/data.php | https://web.archive.org |
| license / terms | **No explicit license or reuse terms found.** Pages checked via archived copies (`data.php`, `notes.txt`, `disclaimer.php`): the disclaimer concerns betting offers, not data reuse. | Archive Terms of Use apply to the archive service; they grant no rights over the underlying third-party content. |
| commercial use | **Unknown — not granted in any text found** | Unknown (depends on the original owner) |
| redistribution | **Unknown** — raw CSVs are therefore NOT committed to this repository | Unknown |
| automated access | Not addressed in the pages checked; the downloader is low-volume (one file per league-season) and identifies itself with a User-Agent | Rate-limited; keep to low volume |
| AI / model-training restrictions | Not addressed; treat as **unknown** | Not addressed |
| attribution | Cite "Football-Data.co.uk" in research outputs (good practice; no requirement found) | Cite the original source and archive URL |
| classification | **RESEARCH_ONLY** | **RESEARCH_ONLY** (only as a labelled fallback, `origin=archive`) |

## football-data.org (API v4, free tier) — S12 ingestion adapter

| Field | football-data.org free tier |
|---|---|
| provider | football-data.org (`daniel@football-data.org`) |
| URL | https://www.football-data.org — API docs https://docs.football-data.org |
| adapter | `src/ingestion/football_data_org.py` (`FootballDataOrgProvider`) |
| rate limit | 10 calls/minute (confirmed via `https://www.football-data.org/pricing`, 2026-10-01) |
| coverage | 12 competitions on the free tier (same page; exact list not enumerated there) |
| license / terms | Homepage states "Access to the top competitions is and will be free forever" — no explicit commercial-use, attribution, redistribution, or AI/model-training terms were found on the pages checked (`pricing`, homepage; `/terms` returned 404 at check time). **Not legal advice — unverified beyond what these pages state.** |
| commercial use | **Unknown — not addressed in the pages checked** |
| redistribution | **Unknown** |
| AI / model-training restrictions | Not addressed; treat as **unknown** |
| why chosen for S12 | Free, no payment method required, email-only registration — the project owner has no budget currently. Picked over API-Football/Sportmonks (also free-tier-capable, but not evaluated) only because it is a known, simple, well-documented REST API. |
| classification | **RESEARCH_ONLY** (same reasoning as football-data.co.uk: no explicit commercial grant found) |

Chosen and connected 2026-10-01 at the project owner's explicit request ("ücretsiz bişeyler
ayarla"); the owner still needs to register for a free API key
(https://www.football-data.org/client/register) and set it as `FOOTBALL_DATA_ORG_API_KEY`
before `configs/ingestion.yaml`'s `football-data-org` entry can be enabled.

## football-data.org — terms READ 2026-10-09 (full "General Terms and Conditions", last updated 2018-06-01)

Text supplied by the owner (the `/terms` URL returned 404 to the automated fetch). Not legal advice.

| Topic | What the terms say |
|---|---|
| commercial use | **Not prohibited and not expressly granted.** The service is "a free API ... (with upgrade options) to the world wide development community"; tiers are only distinguished by competitions, data level and call limits. |
| AI / model training | Not mentioned (so not prohibited). |
| attribution (art. 7) | **Required**: "Football data provided by the Football-Data.org API" in the app/website. Implemented: dashboard footer and `meta.attribution` of every API response. |
| one key, one application (2.3) | A key applies to a single application (one domain for web). Do not share the key across products. |
| after cancelling (9.1) | The football data (fixtures, results, tables, squads) may no longer be referenced on your site or service. Plan for deletion if the subscription ends. |
| logos (9.2) | Team logos belong to their owners; consent is the user's job. This project shows no logos. |
| credentials (6.1) | Keys must not be in open-source repositories. Keys live in `.env` / GitHub secrets only. |
| fair use (3) | Excessive use can be cancelled without notice. |
| contact | info@football-data.org, billing@football-data.org |

**Classification: CONDITIONAL** (no commercial restriction found; conditions above). The remaining risk is that
silence is not a grant: ask info@football-data.org one line ("commercial product, free/paid tier, derived
forecasts and displayed fixtures/results: any objection?") and keep the reply. Raw captured responses from this
source exist as test fixtures in the public repository (`tests/fixtures/real_provider_captures/fdorg_*.json`);
review whether that is acceptable (see art. 9.1).

## API-Football (api-sports) — terms READ 2026-10-09 (text supplied by the owner, Turkish translation of the ToS)

| Topic | What the terms say |
|---|---|
| licence to publish | **None.** "We do not provide a licence for the use and publication of the data in user-created applications, websites or other products. Any licence or permission for publication must be requested by the user from the competent authorities." |
| third-party IP | Some data may be subject to IP / commercial restrictions of leagues, federations or organisers; verifying and obtaining authorisations is the USER's responsibility; "we do not grant any commercial rights over such competitions". |
| betting | Use for betting platforms, TV, fantasy sports or mass media "may require additional licences from the relevant rights holders". |
| logos / images | Identification only; no ownership; use may need the owners' consent. |
| accuracy | As-is, no warranty; wrong/missing data is no ground for a refund. |
| rate limits | Not respecting per-minute limits is a material breach (automatic suspension without notice). |
| free plan | Activated at sign-up; the provider may change or withdraw it at any time. |

**Classification: NO PUBLICATION LICENCE (RESEARCH_ONLY).** The terms do not allow publishing the data; they push
the licensing duty onto the user. Consequences in this project: API-Football data is used internally only
(injury status in stage snapshots and LLM prompts). Public outputs (dashboard, API) show only availability
STATUS labels (OBSERVED/UNKNOWN), never the injury lists. Do not add player-level API-Football data to any public
output. For a bet-related product, rights-holder licences may be needed regardless of the data vendor.

## Consequences
- Benchmark/research results built on any of these sources may be published as methodology and aggregate
  metrics; do not redistribute the raw or normalized data.
- Before the startup MVP (S19) a commercial data source with explicit commercial and redistribution rights
  (or written permission from the provider) is required. football-data.org's free tier is a research/testing
  choice, not a resolved commercial answer — its terms are exactly as unverified as football-data.co.uk's.
  Remaining candidates: API-Football, Sportmonks, football-data.org PAID tiers, Opta/StatsBomb — terms NOT
  yet reviewed.
- The provenance of every file records `origin` so archive copies are never mistaken for official retrieval.

## Open actions (owner)
1. Ask Football-Data.co.uk for written permission for commercial/model-training use, or choose another provider.
2. Register for a free football-data.org API key and set `FOOTBALL_DATA_ORG_API_KEY`; enable it in
   `configs/ingestion.yaml` when ready to actually sync fixtures.
3. Before any commercial/production use, get football-data.org's full terms of service reviewed (their
   `/terms` page was not found at the URL checked here) and record the outcome, same as action 1.
