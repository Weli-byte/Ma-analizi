# ADR 0054 — English UI, phone sign-in, hosting bundle

Status: accepted · 2026-10-13

- **English UI:** `lang` cookie (`/dil/en`, `/dil/tr`); a response middleware translates only the TEXT of the HTML pages (phrase table
  in `src/web/i18n.py`, whole-word, longest first; attributes, URLs, scripts and `/v1` JSON are never touched). A phrase missing
  from the table stays Turkish, never breaks the page. Checked on every page: only club names and the language link remain Turkish.
- **Phone sign-in:** Twilio Verify (`src/web/phone.py`), enabled only with `TWILIO_ACCOUNT_SID/AUTH_TOKEN/VERIFY_SID` (paid per
  SMS). Number normalised to E.164, throttled per number and per client, never stored (hash id + masked name). The live Twilio call
  is NOT verified (no account yet); the flow logic is tested with the SMS call replaced.
- **Cookies:** marked `Secure` whenever the request is HTTPS or arrives with `X-Forwarded-Proto: https`.
- **Hosting:** `deploy/space/` + `scripts/make_space_bundle.py` build a Hugging Face Docker Space bundle (docs/hosting.md). The Space
  reads the forecasts from the private data repo with a READ-ONLY deploy key and a pinned GitHub host-key fingerprint. Not deployed:
  needs the owner's Hugging Face account.
- **Not done:** Free/Pro gating and payments, real push notifications, Apple sign-in, a persistent user database on a host.

## Amendment (2026-11-02): phone sign-in removed
Owner decision: phone (SMS) sign-in is removed from the UI and the backend. It was never configured live (no Twilio account), every SMS would cost
money, and guest + Google sign-in cover the product; the Play listing needs no phone number in the Data safety form. Removed: `src/web/phone.py`,
the routes `/giris/telefon`, `/giris/telefon/kod`, `/giris/telefon/dogrula` (now 404), the login button and its message, the phone page, the
privacy-policy lines about phone sign-in and Twilio, the provider status key `phone`, the readiness item and the tests. No user ever signed in by
phone, so no stored account is affected. Re-adding it needs a new decision (provider, cost, privacy text, Data safety form).
