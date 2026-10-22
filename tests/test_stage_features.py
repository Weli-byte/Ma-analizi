"""UEFA stage features (Sprint 4b, ADR 0069). The stage strings below are the real ones found in the Sprint 1 identity rows of the cached openfootball cups files (2026-10-22)."""

import numpy as np

from src.league_models.stage_features import (
    STAGE_COLUMNS,
    stage_flags,
    stage_matrix,
    stage_table,
    unmatched_stage_texts,
)

REAL_STAGES = ["Group, Matchday 2", "Group G", "Gruppe H", "League phase", "Playoffs", "Playoffs, Matchday 1", "Round of 16", "Quarterfinals", "Semifinals", "Finals, Semifinals",
               "Final", "Finals, Final"]  # fmt: skip


def flags(s):
    return stage_flags(s)


def test_group_and_league_phase_texts_are_league_phase_not_knockout():
    for s in ("Group, Matchday 2", "Group G", "Gruppe H", "League phase"):
        f = flags(s)
        assert f["stage_known"] == 1.0 and f["stage_league_phase"] == 1.0 and f["stage_knockout"] == 0.0 and f["stage_playoff"] == 0.0 and f["stage_final"] == 0.0
    assert flags("Group, Matchday 4")["stage_matchday"] == 4.0 and np.isnan(flags("League phase")["stage_matchday"])


def test_playoffs_knockouts_and_finals_are_told_apart():
    assert flags("Playoffs")["stage_playoff"] == 1.0 and flags("Playoffs")["stage_knockout"] == 0.0 and flags("Playoffs, Matchday 2")["stage_matchday"] == 2.0
    for s in ("Round of 16", "Quarterfinals", "Semifinals", "Finals, Semifinals"):
        f = flags(s)
        assert f["stage_knockout"] == 1.0 and f["stage_two_legged"] == 1.0 and f["stage_final"] == 0.0 and f["stage_league_phase"] == 0.0
    for s in ("Final", "Finals, Final"):
        f = flags(s)
        assert f["stage_final"] == 1.0 and f["stage_knockout"] == 1.0 and f["stage_two_legged"] == 0.0     # a single match


def test_a_domestic_match_has_no_stage_and_is_nan_not_zero():
    f = flags(None)
    assert f["stage_known"] == 0.0 and all(np.isnan(f[c]) for c in STAGE_COLUMNS if c != "stage_known")
    assert flags("")["stage_known"] == 0.0


def test_every_real_stage_text_sets_a_flag_and_the_matrix_aligns_with_the_fixture_ids():
    table = {f"f{i}": s for i, s in enumerate(REAL_STAGES)} | {"dom": None}
    assert unmatched_stage_texts(table) == []
    assert "Mystery round" in unmatched_stage_texts({"x": "Mystery round"})            # an unknown text is reported, never silently classified
    ids = ["f0", "dom", "f8", "missing"]
    m = stage_matrix(ids, table)
    assert m.shape == (4, len(STAGE_COLUMNS)) and m[0, 0] == 1.0 and m[1, 0] == 0.0 and np.isnan(m[1, 1]) and m[2, STAGE_COLUMNS.index("stage_knockout")] == 1.0 and m[3, 0] == 0.0


def test_stage_table_reads_the_identity_rows_without_changing_them():
    class Row:
        def __init__(self, fid, stage, league="UEFA_UCL"):
            self.canonical_fixture_id, self.stage, self.canonical_league_id = fid, stage, league

    rows = [Row("a", "Final"), Row("b", None), Row("c", "Matchday 7", "ENG1")]
    assert stage_table(rows) == {"a": "Final", "b": None}          # a domestic round label is not a competition stage
