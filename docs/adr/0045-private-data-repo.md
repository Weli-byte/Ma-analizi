# ADR 0045 — Raw and derived data live in a private repository

Status: accepted · 2026-10-09 (closes the open item of ADR 0043)

The Odds API terms forbid redistributing its data as downloadable raw files, and this repository is public. The
cloud workflows therefore push to a PRIVATE repository, `Weli-byte/Ma-analizi2`, branches `odds-data` and
`markets-data` (same names as before). Access: a repository-scoped ed25519 deploy key with write access to that
one repo only (secret `DATA_REPO_DEPLOY_KEY` here; the private half never touched disk after being stored), host
keys pinned from GitHub's published list. Local machines fetch with their own git credentials
(`src/data_repo.py`, override with `DATA_REPO_URL`). Existing branches were copied (identical SHAs verified) and the
raw-quote branches removed from the public repository.

Honest limit: commits that were once public may remain reachable by SHA at GitHub for some time and in anyone's
earlier clone; deletion reduces, not erases, past exposure. The data repo must stay private.
