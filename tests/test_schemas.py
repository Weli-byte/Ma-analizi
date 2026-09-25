from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.schemas import (
    ExperimentRecord,
    FeatureSnapshot,
    Fixture,
    FixtureStatus,
    Outcome,
    PredictionRecord,
)


def test_fixture_valid_and_outcome(fixture_kwargs):
    f = Fixture(**fixture_kwargs, status=FixtureStatus.FINISHED, home_goals=2, away_goals=1)
    assert f.outcome == Outcome.HOME
    assert Fixture(**fixture_kwargs).outcome is None


@pytest.mark.parametrize(
    "bad",
    [
        dict(away_id="t1"),
        dict(status=FixtureStatus.FINISHED),
        dict(home_goals=1),
        dict(home_goals=-1, away_goals=0, status=FixtureStatus.FINISHED),
        dict(kickoff_utc=datetime(2024, 1, 1)),
        dict(season="24"),
    ],
)
def test_fixture_invalid(fixture_kwargs, bad):
    with pytest.raises(ValidationError):
        Fixture(**{**fixture_kwargs, **bad})


def test_prediction_valid_and_immutable(pred_kwargs):
    p = PredictionRecord(**pred_kwargs)
    with pytest.raises(ValidationError):
        p.p_home = 0.9


@pytest.mark.parametrize(
    "bad",
    [
        dict(p_home=0.5, p_draw=0.5, p_away=0.5),
        dict(p_home=1.2, p_draw=-0.1, p_away=-0.1),
        dict(model_id="Bad-Id"),
        dict(model_version="v1"),
        dict(feature_version="1"),
        dict(data_version="x"),
        dict(status="bogus"),
    ],
)
def test_prediction_invalid(pred_kwargs, bad):
    with pytest.raises(ValidationError):
        PredictionRecord(**{**pred_kwargs, **bad})


def test_prediction_generated_before_cutoff_rejected(pred_kwargs):
    pred_kwargs["generated_at"] = pred_kwargs["information_cutoff"] - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        PredictionRecord(**pred_kwargs)


def test_prediction_id_deterministic(pred_kwargs):
    a, b = PredictionRecord(**pred_kwargs), PredictionRecord(**pred_kwargs)
    assert a.prediction_id == b.prediction_id
    assert a.model_dump_json() == b.model_dump_json()
    c = PredictionRecord(**{**pred_kwargs, "model_version": "1.0.1"})
    assert c.prediction_id != a.prediction_id


def test_snapshot_leakage_rejected(kickoff):
    cutoff = kickoff - timedelta(hours=1)
    base = dict(
        fixture_id="fx1",
        feature_version="fv1",
        kickoff_utc=kickoff,
        information_cutoff=cutoff,
        generated_at=cutoff,
        values={"elo_home": 1500.0},
    )
    FeatureSnapshot(**base, available_at={"elo_home": cutoff - timedelta(days=1)})
    with pytest.raises(ValidationError, match="leakage"):
        FeatureSnapshot(**base, available_at={"elo_home": kickoff})
    late = kickoff + timedelta(hours=1)
    with pytest.raises(ValidationError):
        FeatureSnapshot(**{**base, "information_cutoff": late, "generated_at": late})


def test_experiment_hash_order_independent():
    kw = dict(
        run_id="r1",
        dataset_version="dv1",
        git_sha="abc1234",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    a = ExperimentRecord(**kw, config={"a": 1, "b": 2})
    b = ExperimentRecord(**kw, config={"b": 2, "a": 1})
    assert a.config_hash == b.config_hash
    assert ExperimentRecord(**kw, config={"a": 2}).config_hash != a.config_hash
