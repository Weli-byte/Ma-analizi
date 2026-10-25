# ADR 0080 - Neutral-venue cards, the remaining ESPN clubs, and a Vercel rollback drill

Date: 2026-10-25. Status: accepted.

## 1. Neutral-venue matches get the experimental card
`RidgePoissonDC.predict(..., neutral=True)` removes the home advantage (the global term plus the competition's deviation) by moving half of it from the nominal home side to the other side: the same "mid level" rule as the product's rate model. The default path is unchanged (tested bit-for-bit); a neutral prediction of (A, B) mirrors that of (B, A). `ExperimentalModel.predict` uses it for cup finals, the block carries `neutral_venue: true`, and the card says so ("Tarafsız saha..."). The live gate (ADR 0073) does NOT count neutral blocks: its registered population is unchanged.

## 2. The remaining ESPN clubs
Two exact aliases of existing clubs (Breidablik to `ISL_breiablik`, Dynamo Kyiv to `UKR_dinamo_kiev`) and six clubs registered without history (Drita Gjilan, Hamrun Spartans, KF Shkendija, Lausanne Sports, Shelbourne, Sigma Olomouc; provenance `no_history_registered`, owner asked for the remaining clubs to be finished, owner to re-confirm). Registering them lets their 2025-26 results enter the history and their forecasts carry the low-confidence label (ADR 0070). The backfill marker now holds a signature of the ESPN names the directory knows, so a new alias makes the window be scanned once more (the merge is idempotent).

## 3. Rollback drill
`scripts/vercel_rollback_drill.sh`, run by the manual workflow `vercel-rollback-drill` in three modes: `dry-run` (reads the production deployment, its predecessors and the live site, changes nothing), `preview` (builds the site bundle exactly like the markets job and deploys it as a preview, not production, then waits for Vercel to report it READY; preview URLs are behind Vercel authentication, so the check is the deployment state), `execute` (rolls production back to the previous deployment, verifies which deployment is production and that the site answers 200, promotes the original again and verifies again; the original is restored by a trap even if a step fails). The markets job redeploys production every four hours, so a failed restore heals itself.

## Limits
A neutral model is the same model with half the home advantage moved: it knows nothing about a particular venue. A club registered without history is forecast at league-average strength and labelled as such. The drill's rollback relies on the Vercel plan's rollback feature; the result of the first real drill is recorded in the Sprint report of this date.
