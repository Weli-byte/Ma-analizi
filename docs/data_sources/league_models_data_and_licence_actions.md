# League models: data gaps and licence actions (2026-10-22)

What I checked today and what is still needed. Not legal advice. "I added" means done in this repository; "owner" means it needs a person (a payment, a signature, a written answer or a key).

## 1. football-data.org: evidence read today (primary pages)
| Question | What the pages say (read 2026-10-22) |
|---|---|
| Commercial use | Blog post "Almost the end of the season: further roadmap" (2015-04-23): "it's only free for non-commercial use." and "If you use the data commercially, please get in contact with me" (for "a fair-minded solution"). The pricing page and the FAQ say nothing about commercial use. |
| Machine-learning / AI use | Not stated on the pricing page, the FAQ or the registration terms. A paid plan called "ML Pack Light" (EUR 29/month, "advanced trend/form data, 10 seasons of history") exists, but the page does not say what it permits legally. |
| Redistribution / publishing | Registration terms 9.1: after cancelling, "the Customer is not permitted to reference the football data"; otherwise not addressed. Team logos and player photos belong to their owners (9.2). |
| Attribution | Terms 7.1 and the FAQ: show "Football data provided by the Football-Data.org API" in a visible place. **I added this line to the shadow dashboard.** |
| API key | Terms 2.3: a key applies to a single application; terms 6.1: no credentials in open-source repositories. |
| Storage after cancelling | Terms 4.6: cancelling "causes the automatic deletion of the Customer's Account, along with all corresponding stored and indexed data." |
| Plans (pricing page) | Free EUR 0 (12 competitions, 10 calls/min, "scores and schedules delayed"); Free + Livescores EUR 12; ML Pack Light EUR 29; Free + Deep Data EUR 29 (line-ups and subs, goal scorers, bookings / cards, squads); Standard EUR 49 (30 competitions); Advanced EUR 99 (50); Pro EUR 199 (100); Odds Add-On EUR 15 (pre-match 1X2 odds); Statistic Add-On EUR 15 (corners, free-kicks, offsides, fouls, possession, shots, cards). Add-ons need a regular plan. |
**Conclusion:** the free tier is documented as non-commercial. Nothing on the site grants commercial display or ML use, and nothing forbids it either: the only way to settle it is a written answer from the operator. So the manifest status stays `CONDITIONAL` (not `PERMITTED`) and, by design, no model fed by this source can be shown to users.
I did NOT edit `configs/league_models/source_licenses.yaml`: its hash is inside the Sprint 5 pre-registration; change it only after the written answer, then re-run the Sprint 5 dev stage and commit the new pre-registration (the consumed holdout is not touched by that).

### Draft message to send (owner)
> To: daniel@football-data.org (address shown on the pricing page); alternative: info@football-data.org
> Subject: Commercial and machine-learning use of football-data.org data (written confirmation request)
>
> Hello, I run a football forecasting product (a web app showing match probabilities for 1X2, over/under 2.5 and both-teams-to-score). I would like a written answer to four questions for the plan I would subscribe to (which one: ______ ):
> 1. May I show predictions to end users in a commercial product (free or paid) that are computed from your fixtures / results / schedule data?
> 2. May I use your data to train and evaluate statistical / machine-learning models (including storing the data for training), and may I keep the trained model after cancelling?
> 3. May I store and publish my own match-by-match predictions that were made using your data, and the track record (predicted probability versus final score), including after cancelling?
> 4. Is the attribution "Football data provided by the Football-Data.org API" sufficient, and are there restrictions on redistributing derived probabilities (not raw data)?
> Please also confirm the price and whether the "ML Pack Light" plan covers (2). Thank you.

When the answer arrives (owner): save it as `docs/data_sources/football_data_org_written_confirmation_<date>.md`, then tell me and I will update the manifest (`plan_status`, the four rights), re-run the dev stage, and re-issue the pre-registration.

## 2. Data gaps and what can be done
| Gap | Why it matters | Source options | Cost / licence | Can I add it? | How you add it |
|---|---|---|---|---|---|
| Running-season results after 2026-09-20 (about 4 rounds) | the licence-clean history ends on 2026-09-20 (`history_latest_kickoff_utc` in every shadow run), so shadow models are 5 weeks stale | the upstream `openfootball/football.json` has NOT been updated since 2026-09-20 (checked today: same 50 / 69 / 36 / 50 / 45 scored matches as our cache); football-data.org results are CONDITIONAL | free but unlicensed for ML; or wait for the maintainers | no (nothing newer exists in the licence-clean source) | wait for upstream, or after the written confirmation (section 1) I can feed football-data.org results into the shadow history behind a flag |
| Europa League and Conference League 2025-26 league phase | cells are `BLOCKED_NO_DATA`, they stay global by default | `openfootball/champions-league` 2025-26 has only `cl.txt` and qualifiers (`clq`, `elq`, `confq`), no `el.txt` / `conf.txt`; football-data.org free tier has no EL / ECL | a paid football-data.org plan (Standard EUR 49 adds competitions; check coverage page) or API-Football (RESEARCH_ONLY) | no | owner decision on a licensed provider; then I add an adapter + licence row |
| Süper Lig 2025-26 (99 of 306 scored) and 2026-27 (no file) | thin Süper Lig history | upstream `tr.1` 2026-27 answers 404 | none free | no | same as above |
| Lineups, injuries, suspensions | needed for "lineup-aware" hypotheses; never a feature today | football-data.org "Free + Deep Data" EUR 29 (line-ups, squads, bookings); FPL injuries (EPL only, RESEARCH_ONLY); API-Football free (information only) | paid / RESEARCH_ONLY | no | subscribe, put the key in `.env` (`FOOTBALL_DATA_ORG_API_KEY` is already used by the free plan), tell me; I add as-of capture (a lineup is only usable once announced, so it can only be collected prospectively) |
| Shots, corners, cards, xG | Premier League tempo, Bundesliga goal density hypotheses | football-data.org Statistic Add-On EUR 15; Highlightly PRO (already verified in ADR 0050); xG: no licensed source | paid | no | same procedure; history before subscription is not available, so models can only use it from the subscription date on |
| Timestamped odds / market baseline | the plan's market baseline, odds-aware card | The Odds API exact odds are being collected by the cloud workflow since 2026-10-08 (CONDITIONAL; no raw redistribution); football-data.org Odds Add-On EUR 15 | key already exists for The Odds API | partly: the market baseline can be built prospectively from the collected exact odds once enough fixtures have results | nothing to do now; ask me again after about 8 weeks of collection |
| UEFA group / knockout stage | context feature for the cups | the openfootball cups files carry stage lines and the Sprint 1 identity rows keep them (`IdentityRow.stage`) | CC0, free | **done** as an opt-in experiment (ADR 0069): 0 of 12 comparisons supported, not adopted | nothing to do |
| Club aliases for football-data.org | the shadow run skipped Torino, Monza, Brest, Nice on its first live run | existing approved aliases of the same strings under `openfootball` | free | **done**: 44 exact-string aliases (Serie A 18, Ligue 1 17, Bundesliga 9) were added with the provenance `fuzzy_approved`, approved_by `claude-assistant 2026-10-22 ... owner to re-confirm`; Premier League and La Liga already resolved | review `configs/team_aliases.yaml` (search for `2026-10-22`) and re-confirm or remove |
| Competition-season gaps from Sprint 1 (10 with no data, Ligue 1 2019-20 incomplete) | fewer training seasons | none better found today | n/a | no | n/a |

## 2b. Urgent fixes done the same day (ADR 0069)
| Problem | What was done | Status |
|---|---|---|
| Results after 2026-09-20 missing | the shadow run now adds finished football-data.org results (already returned by its schedule call) to the history, flagged research-only: +78 results for the four shadow leagues, history ends 2026-10-21 | done, research use only, a model fed by it can never be served to users |
| Süper Lig 2026-27 (football.json 404) | `python -m src.ingestion.openfootball_txt --league TUR1 --season 2026` reads the CC0 file of `openfootball/europe` (306 matches, 36 played, up to 2026-09-07) into the operational cache | done; the source itself is stale for that league; 18 played matches were usable, the rest involve clubs without an alias |
| Europa and Conference League 2025-26 | no free public-domain source; football-data.org answers HTTP 403 for EL with the free key | NOT solvable without a paid plan or another licensed source; stays global |
| UEFA stage information | features built from the stage text and evaluated on the development folds | done; no benefit demonstrated (0 of 12 comparisons), not adopted |

## 3. What the scheduled task does now
`FootballShadowTick` (registered 2026-10-22, every 30 minutes) runs: shadow, reconcile, monitor, scorecard, dashboard. Reconciliation of the 2026-10-23 matches therefore happens by itself; look at `artifacts/league_models/rollout/scorecard.json` or `dashboard.html`. Remove the task with `Unregister-ScheduledTask -TaskName FootballShadowTick -Confirm:$false`.
