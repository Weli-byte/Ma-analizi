# ADR 0077 - Telegram notifications for the cloud jobs

Date: 2026-10-25. Status: accepted. Optional: without the two repository secrets everything below is a no-op.

## Decision
* `src/mlops/notify.py` sends one Telegram message through the Bot API (`sendMessage`, standard TLS verification). The token and chat id come from `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (repository secrets in `shadow-cloud`, `markets-cloud` and the manual `notify-test` workflow, never in a file). A failed send never fails a job and reports only the exception class (a urllib error text can contain the request URL, which contains the token).
* `notify_once` remembers what was announced in a small JSON state file (stored with the shadow stores and the markets artifacts), so one alert or one status change is announced once; the key is stored only after a successful send, so a failure is retried by the next run.
* What is announced: (1) `rollout notify` in `shadow-cloud`: warning or critical alerts raised in the last two hours (at most one message per alert and day) and a shadow family that leaves `SHADOW` (`PROMOTION_CANDIDATE` or `RETIRE`); (2) `src.markets.track`: the experimental card leaving `BETA` (`VERIFIED` or `WITHDRAWN`). Information alerts are never sent.

## Limits
Telegram is an external service: message texts leave the repository's control (they contain no secrets, only alert keys and status words). If the owner deletes the bot or the chat, sends fail silently and the job log shows `FAILED:...`.
