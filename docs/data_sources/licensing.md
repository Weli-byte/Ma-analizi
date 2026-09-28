# Data source licensing register

**Rule:** a free, publicly downloadable dataset is NOT automatically licensed for commercial
redistribution, automated access, or AI/model training. Sources whose terms do not clearly permit these
are classified `RESEARCH_ONLY`, and no startup/production architecture may depend on redistributing them.
Research data sources and commercial production data sources are kept separate.

Last reviewed: 2026-09-26 (by the assistant; **not legal advice — the project owner must verify before any
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

## Consequences
- Benchmark/research results built on this source may be published as methodology and aggregate metrics;
  do not redistribute the raw or normalized data.
- Before the startup MVP (S19) a commercial data source with explicit commercial and redistribution rights
  (or written permission from the provider) is required. Candidates to evaluate in S12: API-Football,
  Sportmonks, football-data.org paid tiers, Opta/StatsBomb — terms NOT yet reviewed.
- The provenance of every file records `origin` so archive copies are never mistaken for official retrieval.

## Open actions (owner)
1. Ask Football-Data.co.uk for written permission for commercial/model-training use, or choose another provider.
2. Record the outcome here and reclassify accordingly.
