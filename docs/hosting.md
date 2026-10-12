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
