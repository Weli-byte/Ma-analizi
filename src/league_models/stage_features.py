"""Sprint 4b (ADR 0069): UEFA competition-stage features (group / league phase, playoff, knockout, final, matchday).

The stage of a UEFA match is published by the source (the openfootball cups files carry `▪ <stage>` lines and the Sprint 1 identity layer keeps it in `IdentityRow.stage`),
so it is known before kick-off for the whole season (the draw and the fixture list are public in advance). It is NOT in the Sprint 4 feature contract (`lm-s4-features-v1`),
which is frozen with the committed Sprint 4 and 5 results; this module adds the columns as an OPT-IN extension evaluated by `stage_experiment.py` on development folds only.

Domestic rows have no stage: `stage_known = 0` and the other columns are NaN (never 0)."""

import re

import numpy as np

STAGE_COLUMNS = ("stage_known", "stage_league_phase", "stage_playoff", "stage_knockout", "stage_final", "stage_two_legged", "stage_matchday")
SCHEMA_VERSION = "lm-s4b-stage-v1"
_MATCHDAY = re.compile(r"Matchday\s+(\d+)", re.I)


def stage_flags(stage: str | None) -> dict[str, float]:
    """Map the source's stage text to the columns. Unknown text is stage_known = 1 with all flags 0 only when it matches nothing: it is reported, never guessed."""
    if not stage:
        return {c: (0.0 if c == "stage_known" else np.nan) for c in STAGE_COLUMNS}
    s = stage.strip()
    low = s.lower()
    league_phase = low.startswith(("group", "gruppe")) or "league phase" in low or low.startswith("league")
    playoff = "playoff" in low or "play-off" in low
    final = bool(re.search(r"(^|,\s*)final$", low)) and "semi" not in low and "quarter" not in low
    knockout = (not league_phase and not playoff) and any(k in low for k in ("round of", "quarter", "semi", "final"))
    two_legged = knockout and not final
    md = _MATCHDAY.search(s)
    return {"stage_known": 1.0, "stage_league_phase": float(league_phase), "stage_playoff": float(playoff), "stage_knockout": float(knockout), "stage_final": float(final),
            "stage_two_legged": float(two_legged), "stage_matchday": float(md.group(1)) if md else np.nan}  # fmt: skip


def stage_table(identity_rows) -> dict[str, str | None]:
    """canonical fixture id -> stage text for the UEFA competitions only, straight from the Sprint 1 identity rows. Domestic rows carry a round label too (`Matchday 7`,
    `12. Round`); it is not a competition stage, so a domestic match stays without a stage (stage_known = 0, other columns NaN)."""
    return {r.canonical_fixture_id: r.stage for r in identity_rows if r.canonical_league_id.startswith("UEFA")}


def stage_matrix(fixture_ids: list[str], table: dict[str, str | None]) -> np.ndarray:
    out = np.full((len(fixture_ids), len(STAGE_COLUMNS)), np.nan)
    for i, fid in enumerate(fixture_ids):
        flags = stage_flags(table.get(fid))
        out[i] = [flags[c] for c in STAGE_COLUMNS]
    return out


def unmatched_stage_texts(table: dict[str, str | None]) -> list[str]:
    """Stage texts that set no flag at all (reported so the taxonomy can be extended on purpose)."""
    bad = set()
    for st in set(table.values()):
        if not st:
            continue
        f = stage_flags(st)
        if not any(f[c] == 1.0 for c in ("stage_league_phase", "stage_playoff", "stage_knockout", "stage_final")):
            bad.add(st)
    return sorted(bad)
