"""S0 DoD: valid prediction record for an example fixture."""

from src.schemas import Fixture, PredictionRecord


def test_example_fixture_to_prediction(fixture_kwargs, pred_kwargs):
    fx = Fixture(**fixture_kwargs)
    rec = PredictionRecord(**{**pred_kwargs, "fixture_id": fx.fixture_id})
    assert abs(rec.p_home + rec.p_draw + rec.p_away - 1) < 1e-9
    assert rec.information_cutoff <= fx.kickoff_utc
