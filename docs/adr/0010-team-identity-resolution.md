# ADR 0010: Team identity resolution

## Status
Accepted — 2026-09-26.

## Context
S1 auto-registered unknown names and fuzzy-matched at 0.85 without provenance; the review queue had no
tooling; a typo could silently create a phantom team.

## Decision
- Store (`configs/team_aliases.yaml`): canonical `teams` (team_id, canonical_name, country) and `aliases`
  (source, raw_name, team_id, valid_from, valid_to, provenance, approved_by, confidence). Aliases are
  scoped by source, country and validity window.
- Unknown names are NEVER mapped automatically (`auto_register_new_teams=false` by default). Similarity
  (difflib) only produces ranked SUGGESTIONS in the review queue; the row is rejected as `unmatched_team`
  and the run fails the quality gate (Q10) until a human approves.
- CLI: `python -m src.data.team_resolution review | suggest | approve | register-team | validate`.
  `approve` records who approved and with what confidence; `validate` detects unknown team ids and
  conflicting/overlapping aliases.
- `data_version` includes the alias store hash.

## Alternatives
Automatic fuzzy matching (rejected: silent errors); external entity registry (future, for S12 scale).

## Consequences
New leagues/seasons require alias approvals. In this remediation seven promoted-team aliases were approved
by the assistant and are flagged `owner review pending` in `approved_by`.
