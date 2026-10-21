# Rollback plan (Sprint 5, for the Sprint 6 rollout)

1. The global model is never removed or modified; every league and target has a GLOBAL route.
2. A canary route is a registry entry with status APPROVED_FOR_CANARY. Rolling back = setting that entry's status to REJECTED or removing it: the router then returns GLOBAL with `NO_APPROVED_MODEL`.
3. An artifact whose hash does not match its registry entry falls back to GLOBAL with an alarm automatically.
4. Production traffic fraction is 0.0 in the policy; nothing here changes what users see.
5. The frozen holdout cannot be re-read; a new decision needs new data (a new frozen period) and an ADR.
