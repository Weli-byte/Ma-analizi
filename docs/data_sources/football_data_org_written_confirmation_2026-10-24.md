# football-data.org: written answer to the licence questions (received 2026-10-24)

Source: e-mail reply from the football-data.org operator to the owner (Veli Parlak / Welliddia), received 2026-10-24 12:14 (local), subject "Commercial and machine-learning use of football-data.org data (written confirmation request)". Transcribed from the text the owner pasted into the assistant session; the owner keeps the original e-mail (keep it unedited, with headers).

## The four questions (sent 2026-10-23) and the answers
Reply text, verbatim:

```
#1 yes
#2 yes
#3 yes
#4 yes

29,-/mt, + setup fee. yes.
```

1. Show predictions computed from their fixtures, results and schedule to end users in a commercial product (free or paid): **yes**.
2. Use the data to train and evaluate statistical / machine-learning models, store it for training, keep the trained models after cancelling: **yes**.
3. Store and publish match-by-match predictions made with the data, with the track record, including after cancelling: **yes**.
4. Attribution "Football data provided by the Football-Data.org API" sufficient, derived probabilities may be redistributed: **yes**.
5. Price: **29 (EUR) per month plus a setup fee** (amount not stated), "yes" to the question whether the ML Pack Light plan covers question 2.

## How the project reads it (and what it does NOT change)
* The "yes" answers belong to the PAID plan quoted (29 EUR / month + setup fee). They are not a statement about the free tier, whose documented terms remain "free for non-commercial use".
* The owner has no budget and has not subscribed. Therefore `configs/league_models/source_licenses.yaml` is NOT changed (its status stays `CONDITIONAL`): the file's hash is inside the Sprint 5 pre-registration, and the evidence for a PERMITTED status (an active subscription to the named plan, the plan name and the setup fee in writing) does not exist yet.
* Data fetched with the free key stays RESEARCH_ONLY in this repository's rules. The product stays free, ad-free and non-commercial until the plan is active.
* Open points to settle in writing before paying: the exact plan name (ML Pack Light?), the amount of the setup fee, whether the plan covers the competitions the product shows (Europa League, Süper Lig return HTTP 403 for the free key), and the attribution text.

## When the plan is active
Add the invoice or subscription confirmation to `docs/data_sources/`, then (one change, one ADR) set the `football_data_org` entry of `source_licenses.yaml` to the new status with this file as evidence, re-run Sprint 5 dev stage and re-issue the pre-registration (see `docs/data_sources/league_models_data_and_licence_actions.md`).
