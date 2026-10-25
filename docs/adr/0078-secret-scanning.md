# ADR 0078 - Secret scanning in CI (gitleaks)

Date: 2026-10-25. Status: accepted.

## Context
The project's rules say "no hard-coded secrets" and the keys live in `.env` (git-ignored) and repository secrets. Until now nothing checked that a key had not been committed by mistake (several keys are in use: football-data.org, The Odds API, API-Football, Highlightly, Twilio, Google, Turso, the data-repo deploy key and now a Telegram bot token).

## Decision
* `.github/workflows/secret-scan.yml` runs on every push to `main`, every pull request, every Monday and on demand. It downloads gitleaks `8.30.1` (a pinned release) and verifies the SHA-256 of the archive before running it (the value comes from the release's own checksums file), then scans the whole git history (`fetch-depth: 0`) with `--redact --exit-code 1`: a finding fails the job and its value is never printed.
* A **canary** runs first: a throwaway repository with a random `api_key = "<32 hex>"` (generated at run time, so no key-like string exists in the workflow file) must be flagged, otherwise the job fails. A scanner that silently stopped working cannot pass for a clean scan.
* `.gitleaks.toml` extends the default rules and contains no active allowlist; every future false positive is a reviewed, commented entry.
* Before enabling it the history was scanned locally with the same binary: 296 commits, 18.75 MB, no leak found.

## Limits
Pattern-based detection finds well-formed keys and high-entropy assignments; it does not find a secret that looks like ordinary text. The private data repository (`Ma-analizi2`) is a separate repository and is not scanned by this workflow. A secret that has been pushed once must be revoked, not just removed; the scan only tells that it happened. The local `.env` is git-ignored and never scanned here.

## Rollback
Delete the workflow; nothing else depends on it.
