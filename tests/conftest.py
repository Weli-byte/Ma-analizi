from datetime import UTC, datetime, timedelta

import pytest

KICKOFF = datetime(2024, 1, 20, 15, 0, tzinfo=UTC)


@pytest.fixture
def kickoff():
    return KICKOFF


@pytest.fixture
def fixture_kwargs():
    return dict(
        fixture_id="fx1",
        league_id="EPL",
        season="2023-24",
        kickoff_utc=KICKOFF,
        home_id="t1",
        away_id="t2",
    )


@pytest.fixture
def pred_kwargs():
    cutoff = KICKOFF - timedelta(hours=24)
    return dict(
        fixture_id="fx1",
        model_id="always_home",
        model_version="1.0.0",
        feature_version="fv1",
        data_version="dv1",
        generated_at=cutoff,
        information_cutoff=cutoff,
        p_home=0.5,
        p_draw=0.3,
        p_away=0.2,
    )
