# ADR 0056: European cups, phase 2

Date: 2026-10-13. Status: accepted. Builds on ADR 0055.

## 1. Domestic leagues as history only
Austria, Belgium, Greece, Netherlands, Portugal and Scotland (first divisions, `at.1 be.1 gr.1 nl.1 pt.1 sco.1` in the
public-domain openfootball repository, verified 2026-10-13) are loaded as `HISTORY_ONLY` leagues (`AUT1 BEL1 GRE1 NED1
POR1 SCO1`). Their results rate the clubs that play the cups; no fixture of these leagues is forecast. 104 clubs were created
(`python -m src.markets.cup_clubs --domestic --apply`); spellings that differ only by club-form words are one club.

## 2. UEFA-style priors (league difficulty + club ranking)
The official UEFA coefficients are not used (publication terms unverified; unreachable from the dev PC). `coefficients.py`
computes the same quantity from the cup results we hold: club points (2 win, 1 draw, 90-minute result) over five seasons,
country coefficient = per-season average of its clubs' points, summed over five seasons. It is an approximation (no qualifying
rounds, no bonus points), leak-free (only results available at the fit time).
It is used as the SHRINKAGE TARGET of a team's strength, not as a rating: `att = (data + k * exp(g)) / (exposure + k)`,
`g = 0.2 * z_country + 0.8 * z_club` (z_country: standardised log of the country coefficient = league difficulty; z_club: the
club's percentile in the club ranking, centred). A team with plenty of data is hardly affected.
Weights chosen by `python -m src.markets.eval_priors` on the VALIDATION seasons only (2022-23, 2023-24; rolling origins
1 Oct / 1 Jan / 1 Mar, next-90-days cup matches, 1X2 log loss, final-test seasons never downloaded): baseline 0.9841, chosen
(0.2, 0.8) 0.9552; paired bootstrap 95% interval of the difference -0.047..-0.011 (excludes zero). The grid optimum is
flat around the choice. Changing the weights needs a new evaluation + ADR.

## 3. Neutral venue (finals)
`StatMatch.neutral`: the final of each cup and the single-leg 2019-20 Champions League quarter/semi-finals (Lisbon). On a
neutral match both sides use the midpoint of the league's home and away level; the home/away levels are estimated from
non-neutral matches only. Forecasts: a cup match from 15 May to the end of June with no other match of that competition
within four days is a final (semi-final second legs are played in pairs and end by 18 May; verified on the 2020-2026 files).
Every other cup match keeps the home/away roles the provider gives. The 2020-21 COVID relocations are not marked (the files
do not say where a match was played).

## 4. Duplicate clubs: owner approval
`python -m src.markets.club_duplicates` lists same-country pairs that look like one club (`reports/club_duplicates.md`),
without pairs that ever met. Nothing is merged without `--apply DROP=KEEP`.

## 5. Probe on a schedule
`probe-cups.yml` runs weekly and summarises unresolved clubs in the run summary.

## 6. Credits
`/v4/sports/{key}/events` costs no credit (provider documentation), so a cup's paid odds call happens only when an event is
inside the 26-hour horizon, at most once per `cup_interval_hours` (6). Idle weeks cost nothing.

## Research: market value and injuries (NOT integrated)
- Transfermarkt market values: no licensed API. `dcaribou/transfermarkt-datasets` is labelled CC0 but is scraped from
  Transfermarkt (terms unaddressed in its README), contains no injuries, and its updates have been paused since July 2026.
  It is not "live" and its legal status is unclear, so it is not used. Project rule: licence-clean sources only.
- Injuries: API-Football lists an injuries endpoint on the free plan (100 requests/day; historical-season limits), but there is
  no history to calibrate an injury effect on, so an injury input could not be validated. Best path: show injuries as
  information only (never in the probability) once the owner supplies a key; a paid plan or a licensed market-value feed is the
  owner's decision.
