from datetime import UTC, datetime, timedelta

import pytest
from conftest import DV
from pydantic import ValidationError

from src.runmode import RunMode
from src.schemas import (
    ExperimentRecord,
    FeatureSnapshot,
    Fixture,
    FixtureStatus,
    Outcome,
    PredictionRecord,
)


# ------------------------------------------------------------------ fixtures / lifecycle
def test_fixture_finished_outcome_and_alias(finished_kwargs):
    f = Fixture(**finished_kwargs)
    assert f.outcome == Outcome.HOME
    assert f.scheduled_kickoff_utc == f.kickoff_utc
    assert f.result_available_at_source == "inferred"


@pytest.mark.parametrize("status", ["scheduled", "postponed", "cancelled", "rescheduled"])
def test_non_played_statuses_have_no_score_or_result_time(fixture_kwargs, status):
    assert Fixture(**fixture_kwargs, status=status).outcome is None
    with pytest.raises(ValidationError):
        Fixture(**fixture_kwargs, status=status, home_goals=1, away_goals=0)
    with pytest.raises(ValidationError):
        Fixture(**fixture_kwargs, status=status, result_available_at_utc=datetime(2024, 2, 1, tzinfo=UTC))


def test_in_progress_and_abandoned_are_valid_without_result_time(fixture_kwargs):
    assert Fixture(**fixture_kwargs, status="in_progress", home_goals=1, away_goals=0).outcome is None
    assert Fixture(**fixture_kwargs, status="abandoned").outcome is None


@pytest.mark.parametrize(
    "patch",
    [
        dict(away_id="t1"),  # same team
        dict(home_goals=None, away_goals=None),  # finished without score
        dict(home_goals=None),  # half a score
        dict(home_goals=-1),
        dict(kickoff_utc=datetime(2024, 1, 1)),  # naive timestamp
        dict(season="24"),
        dict(result_available_at_utc=None),  # finished needs availability time
        dict(result_available_at_source=None),
        dict(result_available_at_utc=datetime(2024, 1, 20, 14, 0, tzinfo=UTC)),  # before kickoff
        dict(finished_at_utc=datetime(2024, 1, 20, 19, 0, tzinfo=UTC)),  # finished after result availability
    ],
)
def test_finished_fixture_invalid(finished_kwargs, patch):
    with pytest.raises(ValidationError):
        Fixture(**{**finished_kwargs, **patch})


# -------------------------------------------------------------------------- predictions
def test_prediction_valid_and_attribute_immutable(pred_kwargs):
    p = PredictionRecord(**pred_kwargs)
    with pytest.raises(ValidationError):
        p.p_home = 0.9
    assert p.status.value == "draft"


@pytest.mark.parametrize(
    "bad",
    [
        dict(p_home=0.5, p_draw=0.5, p_away=0.5),
        dict(p_home=1.2, p_draw=-0.1, p_away=-0.1),
        dict(model_id="Bad-Id"),
        dict(model_version="v1"),
        dict(feature_version="1"),
        dict(data_version="dv1"),  # static labels are no longer valid data versions
        dict(status="bogus"),
        dict(generated_at=datetime(2024, 1, 20, 15, 0, 1, tzinfo=UTC)),  # after kickoff
        dict(information_cutoff=datetime(2024, 1, 20, 16, 0, tzinfo=UTC)),  # cutoff after kickoff
    ],
)
def test_prediction_invalid(pred_kwargs, bad):
    with pytest.raises(ValidationError):
        PredictionRecord(**{**pred_kwargs, **bad})


def test_prediction_generated_before_cutoff_rejected(pred_kwargs):
    pred_kwargs["generated_at"] = pred_kwargs["information_cutoff"] - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        PredictionRecord(**pred_kwargs)


def test_prediction_identity_is_content_sensitive(pred_kwargs):
    a, b = PredictionRecord(**pred_kwargs), PredictionRecord(**pred_kwargs)
    assert a.prediction_id == b.prediction_id and a.model_dump_json() == b.model_dump_json()
    # same logical prediction, different probabilities -> same logical_id, DIFFERENT content/id
    c = PredictionRecord(**{**pred_kwargs, "p_home": 0.6, "p_draw": 0.2})
    assert c.logical_id == a.logical_id
    assert c.content_hash != a.content_hash and c.prediction_id != a.prediction_id
    # any identity field changes the logical id
    for patch in (
        dict(model_version="1.0.1"),
        dict(feature_version="fv3"),
        dict(data_version="dv-aaaaaaaaaaaa"),
        dict(fixture_id="fx2"),
    ):
        assert PredictionRecord(**{**pred_kwargs, **patch}).logical_id != a.logical_id


def test_prediction_status_does_not_change_identity(pred_kwargs):
    a = PredictionRecord(**pred_kwargs)
    b = PredictionRecord(**{**pred_kwargs, "status": "published"})
    assert a.content_hash == b.content_hash


# -------------------------------------------------------------------------- snapshots
def snapshot_kwargs(kickoff):
    cutoff = kickoff - timedelta(hours=1)
    return dict(
        fixture_id="fx1",
        feature_version="fv2",
        data_version=DV,
        kickoff_utc=kickoff,
        information_cutoff=cutoff,
        generated_at=cutoff,
        values={"elo_home": 1500.0, "x": None},
    )


def test_snapshot_leakage_and_versions_rejected(kickoff):
    base = snapshot_kwargs(kickoff)
    cutoff = base["information_cutoff"]
    FeatureSnapshot(**base, available_at={"elo_home": cutoff - timedelta(days=1)})
    with pytest.raises(ValidationError, match="leakage"):
        FeatureSnapshot(**base, available_at={"elo_home": kickoff})
    late = kickoff + timedelta(hours=1)
    with pytest.raises(ValidationError):
        FeatureSnapshot(**{**base, "information_cutoff": late, "generated_at": late})
    with pytest.raises(ValidationError):
        FeatureSnapshot(**{**base, "data_version": "dv1"})
    with pytest.raises(ValidationError):
        FeatureSnapshot(**base, unavailable_reasons={"x": "because"})
    ok = FeatureSnapshot(**base, unavailable_reasons={"x": "new_team"})
    assert ok.unavailable_reasons["x"] == "new_team"


def test_snapshot_containers_are_deeply_immutable(kickoff):
    s = FeatureSnapshot(**snapshot_kwargs(kickoff), available_at={"elo_home": kickoff - timedelta(days=2)})
    with pytest.raises(TypeError):
        s.values["elo_home"] = 9.0
    with pytest.raises(TypeError):
        s.available_at["new"] = kickoff
    assert '"elo_home":1500.0' in s.model_dump_json().replace(" ", "")


# ------------------------------------------------------------------------ experiments
def experiment_kwargs(**over):
    kw = dict(
        experiment_id="e1",
        run_mode=RunMode.RESEARCH,
        git_sha="a" * 40,
        git_dirty=False,
        python_version="3.12.1",
        platform="test",
        dependency_lock_hash="b" * 64,
        data_version=DV,
        feature_version="fv2",
        config={"a": 1, "nested": {"b": [1, 2]}},
        model_name="always_home",
        model_version="1.0.0",
        seed=1,
        split_id="split-x",
        train_rows=10,
        validation_rows=5,
        final_test_rows=0,
        metrics={"log_loss": 1.0},
        created_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
    )
    kw.update(over)
    return kw


def test_experiment_hash_order_independent_and_config_frozen():
    a = ExperimentRecord(**experiment_kwargs(config={"a": 1, "b": 2}))
    b = ExperimentRecord(**experiment_kwargs(config={"b": 2, "a": 1}))
    assert a.config_hash == b.config_hash
    assert ExperimentRecord(**experiment_kwargs(config={"a": 2})).config_hash != a.config_hash
    with pytest.raises(TypeError):
        a.config["a"] = 5
    nested = ExperimentRecord(**experiment_kwargs())
    with pytest.raises(TypeError):
        nested.config["nested"]["b"] = 0


@pytest.mark.parametrize(
    "patch",
    [
        dict(git_sha="unknown"),  # unknown SHA outside development
        dict(dependency_lock_hash="missing"),
        dict(run_mode=RunMode.STRICT, git_dirty=True, dirty_files=("a",)),
        dict(git_dirty=True),  # dirty without file list
        dict(final_test_rows=3),  # final rows outside final mode
        dict(data_version="dv1"),
        dict(model_name="Bad Name"),
        dict(git_sha="XYZ"),
    ],
)
def test_experiment_provenance_enforced(patch):
    with pytest.raises(ValidationError):
        ExperimentRecord(**experiment_kwargs(**patch))


def test_experiment_development_may_have_unknown_sha():
    e = ExperimentRecord(
        **experiment_kwargs(run_mode=RunMode.DEVELOPMENT, git_sha="unknown", dependency_lock_hash="missing")
    )
    assert e.git_sha == "unknown"
    dirty = ExperimentRecord(**experiment_kwargs(git_dirty=True, dirty_files=("x.py",)))
    assert dirty.dirty_files == ("x.py",)
    final = ExperimentRecord(**experiment_kwargs(run_mode=RunMode.FINAL, final_test_rows=4))
    assert final.final_test_rows == 4


def test_fixture_status_enum_covers_lifecycle():
    assert {s.value for s in FixtureStatus} == {
        "scheduled", "postponed", "in_progress", "finished", "abandoned", "cancelled", "rescheduled",
    }  # fmt: skip
