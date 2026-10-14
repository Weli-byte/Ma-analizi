# Putting the app on the internet (free): Hugging Face Space

Why this route: free, no credit card, Docker supported, public URL you can show to someone. Limits: the disk is ephemeral
(favourites reset when the Space restarts) and a free Space sleeps after about 48 hours without visitors (it wakes on the next
visit). Render's free web service is the alternative (no card; sleeps after 15 minutes). A paid host or a database is needed for
a real product (see ADR 0054).

## Steps (about 15 minutes)
1. Make a free account at huggingface.co. Create a new Space: SDK **Docker**, visibility Public (a free Space must be public).
2. In the Space, Settings -> Variables and secrets -> add these **secrets**:
   - `DATA_REPO_DEPLOY_KEY`: a NEW read-only deploy key for `Weli-byte/Ma-analizi2` (GitHub -> that repo -> Settings -> Deploy keys
     -> Add key, leave "Allow write access" OFF; paste the private half into the secret). Do not reuse the writer key.
   - `FORECAST_SESSION_SECRET`: any long random text (keeps logins valid across restarts).
   - optional later: `GOOGLE_CLIENT_ID`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SID`.
3. On your PC: `python scripts/make_space_bundle.py` (writes `dist/space/`), then push that folder to the Space's git repo
   (`git init`, `git remote add space https://huggingface.co/spaces/<you>/<name>`, `git add -A`, `git commit`, `git push space HEAD:main`).
4. The Space builds, starts `space_start.sh`, pulls the forecasts from the private data repo every 20 minutes and serves the app.
   Open the Space URL: login screen first, guest works.

## Before the URL is public
- **Logos stay off** (`FORECAST_UI_LOGOS` is not set): club logos are trademarks. The app shows neutral initials badges.
- Raw bookmaker prices are never shown (data terms). Keep it that way.
- Football-Data.org attribution and "Gamble Responsibly. 18+" are already in every page.
- Check the legal side of betting content in your country first (ADR 0036 / docs/data_sources/provider_evaluation.md).

# Option 2: Vercel (a snapshot deployment)

Vercel runs the app as a serverless function, so this is a SNAPSHOT of today's forecasts, good for showing the project;
it does not refresh itself and favourites live in `/tmp` (lost when the function restarts). For a live, self-refreshing site
use the Space above.

1. `python -m src.markets.run --sync-remote` (pulls the newest forecasts and injuries from the private data repo).
2. `python scripts/make_vercel_bundle.py` writes `dist/vercel/` (code, configs, the newest forecast per fixture, no secret).
3. In `dist/vercel`: `npx vercel --prod` (log in with the free account once). In the Vercel project settings add the environment
   variables `FORECAST_SESSION_SECRET` (a long random string), `GOOGLE_CLIENT_ID` and, only if logos are wanted,
   `FORECAST_UI_LOGOS=1` (the logo images are not in the bundle, so leave it off).
4. Add the Vercel address to the Google client's "Authorised JavaScript origins" (Google Cloud console).
5. To refresh the data, repeat steps 1-3.
Privacy and terms pages: `/gizlilik` and `/kosullar` are public (needed for the Google consent screen); they are DRAFTS and
need a lawyer's review before a public launch.
A persistent user database: set `FORECAST_USER_DB` to a path on a disk that survives restarts (a host's volume).
