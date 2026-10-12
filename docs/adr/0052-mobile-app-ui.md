# ADR 0052 — Mobile-app style UI with sign-in (supersedes the page layout of ADR 0051)

Status: accepted · 2026-10-12 (owner: "Misli-like mobile app, login first, green and white, per-league screens, logos")

- **Look:** green/white mobile layout (max 480 px column, bottom navigation: Ana sayfa, Ligler, Tavsiyeler, Profil), 1 / X / 2
  probability boxes (probabilities, not odds), PWA manifest + service worker + generated icon so it installs from the browser
  ("Ana ekrana ekle"). Native App Store / Google Play apps are NOT built (they need paid developer accounts and a native
  shell); the PWA is the route that works today.
- **Screens:** login first (`/giris`); home (leagues, today's matches, strongest tips); leagues grid; one screen per league
  (`/lig/{KEY}`) with week chips (bu hafta / gelecek hafta / hepsi) and matches grouped by day; match detail; tips; profile
  (track record, method, logout).
- **Sign-in:** Google (ID token checked with Google's tokeninfo: audience, issuer, expiry, verified e-mail; needs
  `GOOGLE_CLIENT_ID`), Apple (needs a paid Apple Developer account) and phone/SMS (needs an SMS provider) are shown on the
  login screen but say "not enabled yet" until configured: nothing is simulated. "Misafir olarak devam et" works today.
  Sessions are signed, expiring, HttpOnly cookies (`FORECAST_SESSION_SECRET`); no password or phone number is stored.
  Google live verification is untested until the owner creates a client id.
- **Logos:** shown only with `FORECAST_UI_LOGOS=1` (local demonstration, e.g. for a teacher), from the private logo store;
  public deployments must leave it off (trademarks, Highlightly terms 6.3): neutral initials badges are shown instead.
- Still not shown: raw bookmaker prices/names, player-level injury data.
