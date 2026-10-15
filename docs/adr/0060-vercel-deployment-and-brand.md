# ADR 0060: Vercel deployment with automatic refresh, app name and owner details

Date: 2026-10-14. Status: accepted.

## Hosting
Hugging Face Docker Spaces now need a paid plan (seen in the account's "Create a new Space" page, 2026-10-14); Static Spaces are free
but cannot run the app. The free path is Vercel (Hobby): `scripts/make_vercel_bundle.py` writes `index.py` at the bundle root
(Vercel's FastAPI runtime finds `app` there; an `api/index.py` + rewrite layout answered 404 on every path), the newest forecast per
fixture, the track record, the results store (referees, scorers) and the newest injuries. The `markets-cloud` job redeploys it every
run (about every 4 hours; free plan allows 100 deployments a day) when `VERCEL_TOKEN`, `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID`
are repository secrets. The Vercel-GitHub integration of the project was disconnected: otherwise every push built the repository root
as the site and failed. Logos stay off (trademarks, not in the bundle); favourites live in /tmp and can reset on a restart.
Live: https://mac-tahmin-eight.vercel.app (verified: login, guest, leagues, match page, privacy, track record, Google sign-in).

## Brand and legal identity
`src/web/brand.py`: name **Welliddia**, owner Veli Parlak, contact and data-deletion address veliparlak84@gmail.com, country Türkiye
(each overridable by an environment variable). The privacy page names the data controller, where data is processed (Vercel, Google,
Twilio: United States), retention (until the user deletes it) and the user's rights under KVKK article 11; the terms name the
service provider and the governing law. Both stay DRAFTS: a lawyer must review them before a public launch; a betting-content
check in Türkiye is still open.

## Google sign-in verified live (2026-10-15)
A Google OAuth client (type Web, origin `https://mac-tahmin-eight.vercel.app`, `GOOGLE_CLIENT_ID` set in Vercel and as a repository
secret) is in use; the consent screen is published ("In production", scopes `openid`, `email`, `profile` only, so no Google
verification review). Verified by the owner: sign-in with the owner's account, and with a second Google account that is not a test
user, in a private window, with no "unverified app" warning. Privacy and terms pages are public on the live site.
