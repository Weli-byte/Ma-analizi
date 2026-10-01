"""S9: leaderboard tabulation. Duck-typed fake results (not a real EvalReport) -- the module
doesn't import `runner`, so neither does this test, matching that decoupling."""

from dataclasses import dataclass, field

import pytest

from src.evaluation.leaderboard import build_leaderboard, render_leaderboard_md


@dataclass
class FakeResult:
    model_id: str
    model_class: str
    metrics: dict
    confidence_intervals: dict = field(default_factory=dict)
    by_league: dict = field(default_factory=dict)
    by_season: dict = field(default_factory=dict)


def two_models():
    return [
        FakeResult(
            "elo", "statistical", {"n": 100, "log_loss": 0.95, "accuracy": 0.5},
            by_league={"EPL": {"n": 60, "log_loss": 0.9}, "LALIGA": {"n": 40, "log_loss": 1.02}},
            by_season={"2023-24": {"n": 100, "log_loss": 0.95}},
        ),  # fmt: skip
        FakeResult(
            "market_implied", "reference_market_baseline", {"n": 100, "log_loss": 0.90, "accuracy": 0.53},
            by_league={"EPL": {"n": 60, "log_loss": 0.88}, "LALIGA": {"n": 40, "log_loss": 0.93}},
            by_season={"2023-24": {"n": 100, "log_loss": 0.90}},
        ),  # fmt: skip
    ]


def test_global_scope_has_one_row_per_model():
    lb = build_leaderboard(two_models())
    assert {r.model_id for r in lb["global"]} == {"elo", "market_implied"}
    assert all(r.scope == "global" for r in lb["global"])


def test_by_league_and_by_season_expand_into_one_row_per_group():
    lb = build_leaderboard(two_models())
    league_scopes = {r.scope for r in lb["by_league"]}
    assert league_scopes == {"league:EPL", "league:LALIGA"}
    assert len(lb["by_league"]) == 4  # 2 models x 2 leagues
    assert {r.scope for r in lb["by_season"]} == {"season:2023-24"}


def test_n_is_extracted_and_not_duplicated_as_a_metric_in_grouped_scopes():
    lb = build_leaderboard(two_models())
    epl_row = next(r for r in lb["by_league"] if r.model_id == "elo" and r.scope == "league:EPL")
    assert epl_row.n == 60
    assert "n" not in epl_row.metrics
    assert epl_row.metrics["log_loss"] == 0.9


def test_build_leaderboard_does_not_mutate_input_by_league_dicts():
    models = two_models()
    before = dict(models[0].by_league["EPL"])
    build_leaderboard(models)
    assert models[0].by_league["EPL"] == before  # "n" must still be there afterward


def test_by_model_class_groups_without_duplicating_global_rows():
    lb = build_leaderboard(two_models())
    assert len(lb["by_model_class"]) == len(lb["global"])
    classes = {r.model_class for r in lb["by_model_class"]}
    assert classes == {"statistical", "reference_market_baseline"}


def test_render_leaderboard_md_has_no_single_winner_label():
    lb = build_leaderboard(two_models())
    md = render_leaderboard_md(lb, ["log_loss", "accuracy"])
    assert "No single overall winner" in md
    assert "winner" not in md.lower().replace("no single overall winner", "")
    assert "elo" in md and "market_implied" in md
    assert "### global" in md and "### by_league" in md and "### by_season" in md


def test_render_leaderboard_md_handles_empty_leaderboard():
    md = render_leaderboard_md({"global": [], "by_league": [], "by_season": [], "by_model_class": []}, ["log_loss"])
    assert "No single overall winner" in md


@pytest.mark.parametrize("scope", ["global", "by_league", "by_season"])
def test_every_row_confidence_intervals_default_to_empty_when_absent(scope):
    lb = build_leaderboard(two_models())
    assert all(isinstance(r.confidence_intervals, dict) for r in lb[scope])
