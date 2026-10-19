# ADR 0062: Free / Pro plan infrastructure (no payment)

Status: accepted (2026-10-15). Verified live 2026-10-15 (see Live check).

## Decision
Build the plan model now, sell nothing. The feature matrix is `docs/product.md`; `src/web/plans.py` holds the web subset
(`paper_value` = `/tavsiyeler`, `forecast_history` = `/basari-kaydi`, both Pro).

- **Gates are OFF by default.** Without `FORECAST_PLANS_ENFORCED` every signed-in user sees everything, so the live site does not
  change. The owner turns them on (Vercel environment variable) only when a Pro plan exists to be sold.
- **Who is Pro:** the stored plan (`users.plan`, default `free`, set by `UserStore.set_plan`, the hook a future payment webhook
  will call) or a verified e-mail listed in `FORECAST_PRO_EMAILS`. Only Google sign-in puts an e-mail on the session, after the
  `email_verified` check, so guests and phone users cannot match the list. This lets the owner test Pro with their own account.
- A gated page answers 403 with a "Pro feature, not on sale yet" page. It never advertises a price or links to a payment.
- **Migration:** `users.plan` is added with `ALTER TABLE ... ADD COLUMN` when `PRAGMA table_info(users)` shows no `plan` column,
  for both the SQLite file and Turso. Existing rows become `free`. `touch()` never rewrites the plan.

## Not decided (owner)
Which features are Pro for the first release, the price, the payment provider and the checkout. Charging money also needs the
legal items in ADR 0036 (provider terms, a lawyer-reviewed terms page, consumer-law pages for Türkiye).

## Tests
`tests/test_plans.py`: gates off/on, e-mail grant, stored plan survives a sign-in, migration of an old table, 403 and profile plan
label. The Turso `PRAGMA` migration is unverified against the real service until the next deploy.

## Live check (2026-10-15)
After the deploy of this change, on the real Turso database: guest sign-in, add favourite and list it, profile shows `Plan: Ücretsiz`
(the `plan` column exists and is readable, so the `PRAGMA`-based migration worked), `/tavsiyeler` and `/basari-kaydi` answer 200 with gates off.
The 403 path with `FORECAST_PLANS_ENFORCED` set was tested locally only. The test account was deleted afterwards.

## Amendment 2026-10-19: package B chosen by the owner
- Pro: the match-page tabs "Marketler" (over/under, both teams to score, scores, corners, cards), "Hakem" and "Oyuncular".
  Free: 1X2 forecast, the summary tab, form, table, injuries, tips (`/tavsiyeler`) and the whole track record (`/basari-kaydi`).
  Reason: the 130-match record (reports/model_comparison_2026-10-19.md) shows no model advantage over the market, so the track
  record stays open (transparency) and the bet-suggestion page is not sold (the terms say it is not betting advice).
- The first draft gated `/tavsiyeler` and `/basari-kaydi`; that gate and its 403 page are removed. Locked tabs render a
  "Pro planına ait" notice instead of their content and their button says "(Pro)".
- The owner uses Pro for free: the contact address (`brand.CONTACT_EMAIL`) is always Pro when it is the verified Google e-mail.
- Gates are still OFF (`FORECAST_PLANS_ENFORCED` unset). Turning them on is the owner's call, not before payment exists.
- Not gated, to be honest about it: the over/under and BTTS numbers also appear in the summary tab and the tips list (free).