# ADR 0077 - Telegram notifications for the cloud jobs

Date: 2026-10-25. Status: accepted. Optional: without the two repository secrets everything below is a no-op.

## Decision
* `src/mlops/notify.py` sends one Telegram message through the Bot API (`sendMessage`, standard TLS verification). The token and chat id come from `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (repository secrets in `shadow-cloud`, `markets-cloud` and the manual `notify-test` workflow, never in a file). A failed send never fails a job and reports only the exception class (a urllib error text can contain the request URL, which contains the token).
* `notify_once` remembers what was announced in a small JSON state file (stored with the shadow stores and the markets artifacts), so one alert or one status change is announced once; the key is stored only after a successful send, so a failure is retried by the next run.
* What is announced: (1) `rollout notify` in `shadow-cloud`: warning or critical alerts raised in the last two hours (at most one message per alert and day) and a shadow family that leaves `SHADOW` (`PROMOTION_CANDIDATE` or `RETIRE`); (2) `src.markets.track`: the experimental card leaving `BETA` (`VERIFIED` or `WITHDRAWN`). Information alerts are never sent.

## Limits
Telegram is an external service: message texts leave the repository's control (they contain no secrets, only alert keys and status words). If the owner deletes the bot or the chat, sends fail silently and the job log shows `FAILED:...`.

## Update 2026-10-25 (same day): what is worth a message, and a dead-man switch
The first day of real alerts (93 alerts in the shadow store, 26 shadow runs) showed what is noise: 59 were information (never sent), 8 `slow_inference` warnings (structural: the per-fixture time includes the boosted-tree feature rebuild), `provider_error` once (a TLS handshake timeout on two competitions; the next run was fine), `artifact_store_missing` (information in the cloud, see `monitoring.check_registry`) and one stale critical `records_refused` of 22 October. Policy now (`notify.should_notify`): every critical alert; of the warnings only `scheduler_stale`, `no_shadow_run`, `provider_not_configured`, `model_missing`, `fallback_rate`, `outcome_lag`, `drift_critical`, `performance_regression:*`. Three failed provider runs in a row stay critical (`provider_unavailable`).
A job cannot report its own death. The markets job (a separate schedule) runs `python -m src.mlops.notify --watchdog artifacts/markets/shadow_gate.json --max-hours 6`: the shadow job stamps that file on every run, so a stamp older than six hours (or no file) sends one message per day. The markets job itself is not watched this way; GitHub e-mails a failed workflow to the owner.
