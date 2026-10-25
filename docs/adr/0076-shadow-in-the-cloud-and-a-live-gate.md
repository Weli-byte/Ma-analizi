# ADR 0076 - The shadow layer runs in the cloud and has a pre-registered live gate

Date: 2026-10-25. Status: accepted. Builds on ADR 0068 (shadow layer), 0071 (exploratory families) and 0073 (the same idea for the card).

## Context
The shadow records were written by a Windows scheduled task on the owner's PC. A record can only be written inside a 3-hour window before kick-off (t-24h); with the PC off the match's record is lost for good. And nothing turned the shadow evidence into a verdict: somebody had to open the scorecard and interpret it.

## Decision
1. **`.github/workflows/shadow-cloud.yml`** (every 30 minutes, public repository, so the Actions minutes are free): restores the append-only stores from the `shadow-data` branch of the private data repo, warms the open-source history cache (`rollout prepare`, the same fetch the product's loader does), runs `shadow`, `reconcile`, `monitor`, `scorecard`, `gate`, and publishes the stores back to `shadow-data`. The football-data.org key and the data-repo deploy key are repository secrets (the same ones the markets job uses). The branch was seeded on 2026-10-25 with the PC's 256 records, 4 outcomes and 214 score forecasts, so no record was lost. The Windows `FootballShadowTick` is disabled once the cloud job has run successfully (two writers to two stores would diverge).
2. **Pre-registered gate** (`configs/league_models/shadow_gate.yaml`, version `shadow-gate-v1`, hash-pinned in `tests/test_shadow_gate.py`; `rollout/gate.py`, command `rollout gate`). For each exploratory family (`P_dc_sep`, `H_dc_hier`) and each rule target (over/under 2.5, BTTS) the family's t-24h record is paired with the global record of the same fixture and snapshot and the log losses are compared with a paired block bootstrap (ISO weeks, 95%). `RETIRE` when at least 100 pairs and for one target the whole interval is above 0; `PROMOTION_CANDIDATE` when at least 150 pairs and for both targets the whole interval is below 0; otherwise `SHADOW`. 1X2 is reported as information only. A promotion candidate is a recommendation: nothing is routed (kill switch ON, canary 0, football-data.org schedule CONDITIONAL, football-data.org plan not subscribed).
3. **Visible**: the markets cloud job copies `shadow_gate.json` from `shadow-data` into `artifacts/markets/`; the API's track record carries it (`shadow_gate`) and the track page shows "Gölge modeller: canlı kayıt" with the numbers, the interval and the rule.

## Limits
* Scheduled GitHub workflows can be delayed by minutes and are paused after long repository inactivity; the 3-hour window and the data pushes make a pause unlikely, and a missed window loses only that stage record.
* The model version of a record depends on the history digest, so a later run can write a new version for the same fixture; the scorecard already uses the latest version per fixture, stage and family.
* The cloud history is the licence-clean public-domain history plus the research overlay of football-data.org results, exactly as on the PC; those records stay research evidence.
* No alert channel (e-mail, Telegram) yet: alarms are in `alerts.jsonl` and in the workflow log.

## Rollback
Disable the workflow (GitHub Actions page or delete the file), re-enable the Windows task (`scripts/install_shadow_task.ps1`), copy `shadow-data` back to `artifacts/league_models/rollout/` first so the stores continue.

## Update 2026-10-25 (same day): self-restarting loop instead of a 30-minute cron
On the first day GitHub's cron fired late or not at all for new and old workflows (the markets job was almost an hour late, the shadow job's own cron never fired in two hours). A pre-match record can only be written in a 3-hour window, so the job now runs five rounds ten minutes apart inside one run (`scripts/shadow_cloud_loop.sh`), then starts itself again with `gh workflow run` (the same self-restarting loop as odds-exact, ADR 0049; `SHADOW_LOOP=off` as a repository variable stops it). The cron stays as an hourly backup that revives a stopped loop. The stores are published after a round that changed them and at least every third round (the stamp in `shadow_gate.json` is what the markets job's watchdog reads, ADR 0077).

## Update 2026-10-25: the public track page, checked with the real files
Rendering the page from the real cloud files showed four things, all fixed: the Vercel bundle copied only `track_record.json`, so the deployed site would not have had the shadow verdict (`shadow_gate.json` is now bundled; the bundle also creates the markets directory when there is no forecast artifact yet); the two live-record sections were hidden while no match was scored yet (they show now); an interval from one or two ISO weeks collapsed onto a point (`[+0.0144, +0.0144]`) and looked like a verdict (it is printed only from four weeks on, "az hafta var" before); and the status text of the card section was English on the Turkish page (now Turkish, with an English translation).
