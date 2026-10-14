# ADR 0057: absent players shown as information only

Date: 2026-10-14. Status: accepted.

## Decision
Injuries and suspensions are collected from the API-Football FREE plan (`/injuries?date=`; verified 2026-10-14 with the
owner's key: 168 Champions League entries for one day, one call covers every league) and shown on the match page as an
"Eksikler" card. They NEVER enter a probability: there is no historical injury data to calibrate an effect on, so a
weight would be a guess (project rule: no fake or imputed data).

## Constraints, stated on the page
- The free plan only answers dates in [yesterday, tomorrow]; the card says so when nothing was observed and never reads
  "nobody is out" without an observation. An empty side says "no absence recorded in the source".
- No report time exists per entry: `observed_at` is our fetch time and is shown. Each stored file is immutable and carries the
  SHA-256 of the raw response; an unchanged response is not stored again.
- 100 requests/day: two calls (today, tomorrow) per 4-hour cloud run, about 12/day.
- A club the directory cannot resolve is dropped and counted (`unresolved_clubs`), never guessed.
- Status mapping: `Questionable` = doubtful, a reason with suspension/card = suspended, non-medical reasons (roster, national
  team, personal) = not in the squad, everything else = injured.
- Terms of the free plan for commercial use are unverified (RESEARCH_ONLY, as for every provider here).

## Where
`src/markets/injuries.py`, collected by `markets-cloud.yml` into `artifacts/injuries/<day>/` on the data branch (3 days kept),
synced to the PC by `python -m src.markets.run --sync-remote`, rendered by `pages.injuries_card`.

## Not done
Market values (no licensed live source, ADR 0056) and any effect of absences on the forecast.

## S19 preparation done in the same step (code only; the owner-side steps stay open)
- `/gizlilik` and `/kosullar`: public privacy and terms pages (Turkish + English), linked from the login screen and the profile.
  DRAFTS, not legal advice: a lawyer must review them before a public launch (and a betting-content check is still open).
- `FORECAST_USER_DB`: the user database can live on a persistent volume (default stays `artifacts/users.sqlite`).
- `scripts/make_vercel_bundle.py` + `docs/hosting.md` (Option 2): a snapshot deployment for Vercel. The Space start script now
  also syncs the injuries.
- Sign in with Apple: `auth.validate_apple_token` verifies Apple's RS256 identity token (pure-Python RSASSA-PKCS1-v1_5, no new
  dependency) against Apple's published keys (cached 1 h): signature, issuer, audience, expiry. Tested only with a locally generated
  key pair; NOT verified against the real Apple service, which needs `APPLE_CLIENT_ID` (a Services ID of a paid Apple Developer
  account). The button stays off until the variable is set. The Apple script cannot be pinned with an integrity hash (Apple serves a
  versioned file and publishes no hash); the same holds for Google's script.
- Not coded on purpose: payments and the Free/Pro split (decisions + accounts first), real push notifications (needs VAPID keys and a
  push library; a design decision), SMS provider.
