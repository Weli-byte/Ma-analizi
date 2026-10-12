# ADR 0053 — Favourites, search, charts, project summary

Status: accepted · 2026-10-12

- `src/web/store.py`: SQLite `artifacts/users.sqlite` (never committed): users (uid, provider, name, email, timestamps) and favourite
  clubs. No password, phone number or token is stored. Guests get a random uid in the signed session cookie; Google users `google:<sub>`.
  "Verilerimi sil" deletes the user's rows. On a host with an ephemeral disk the file resets on restart.
- Favourites screen, a home banner when a favourite plays this week (in-app only: real push notifications need a push service
  and are NOT built), team search (`/ara`), a public project summary (`/sunum`).
- Artifacts now carry the last five results of each team (`form`, known at the cut-off) and the total-goal distribution
  (`goals.total_goals_distribution`); the match screen draws both. Older artifacts simply lack them.
