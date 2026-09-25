import pytest
from pydantic import ValidationError

from src.config import EvaluationConfig, ProviderConfig, load_config


@pytest.mark.parametrize("name", ["data", "model", "evaluation", "provider"])
def test_load_all(name):
    assert load_config(name) is not None


def test_unknown_config():
    with pytest.raises(KeyError):
        load_config("nope")


def test_final_test_cannot_be_touched():
    with pytest.raises(ValidationError):
        EvaluationConfig(
            split_strategy="expanding",
            min_train_seasons=1,
            metrics=[],
            calibration_bins=10,
            final_test_touched=True,
        )


def test_provider_rejects_literal_secret():
    with pytest.raises(ValidationError):
        ProviderConfig(providers={"x": {"api_key_env": "sk-abc123", "model": "m"}})
