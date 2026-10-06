import pytest
from pydantic import ValidationError

from src.config import (
    CONFIG_DIR,
    EvaluationConfig,
    LeagueFormat,
    ProviderConfig,
    SourcesConfig,
    load_config,
)

EVAL_OK = dict(
    split_strategy="expanding",
    min_train_seasons=1,
    metrics=["log_loss"],
    calibration_bins=10,
    bootstrap_samples=10,
    max_fallback_rate=dict(development=1, research=0.1, strict=0.05, final=0.05),
    train_seasons=["2019-20"],
    validation_seasons=["2020-21"],
    final_test_seasons=["2021-22"],
)


@pytest.mark.parametrize(
    "name", ["data", "leagues", "sources", "features", "model", "evaluation", "provider"]
)
def test_load_all(name):
    assert load_config(name) is not None


def test_unknown_config():
    with pytest.raises(KeyError):
        load_config("nope")


def test_split_validation_rules():
    EvaluationConfig(**EVAL_OK)
    for patch in (
        dict(validation_seasons=["2019-20"]),  # overlap
        dict(train_seasons=["2021-22"], final_test_seasons=["2019-20"]),  # not chronological
        dict(final_test_seasons=[]),  # final test must exist
        dict(metrics=["mystery"]),
        dict(calibration_bins=1),
    ):
        with pytest.raises(ValidationError):
            EvaluationConfig(**{**EVAL_OK, **patch})


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        EvaluationConfig(**EVAL_OK, final_test_touched=False)  # the old text-only guard is gone


def test_real_evaluation_split_is_chronological_and_final_excludes_partial_season():
    cfg = load_config("evaluation")
    flat = [*cfg.train_seasons, *cfg.validation_seasons, *cfg.final_test_seasons]
    assert flat == sorted(flat) and "2026-27" not in flat  # current partial season is in no split


def test_provider_rejects_literal_secret():
    with pytest.raises(ValidationError):
        ProviderConfig(providers={"x": {"api_key_env": "sk-abc123", "model": "m"}})


def test_league_format_is_derived_not_hardcoded():
    fmt = LeagueFormat(
        source_code="X", country="XXX", name="n", tier=1, source_timezone="UTC",
        n_teams=18, rounds=2, season_start_month=7, season_end_month=6,
    )  # fmt: skip
    assert fmt.matches_per_team == 34 and fmt.matches_per_season == 306
    fmt3 = fmt.model_copy(update={"n_teams": 12, "rounds": 3})
    assert fmt3.matches_per_team == 33 and fmt3.matches_per_season == 198


def test_sources_never_configure_tls_bypass():
    text = (CONFIG_DIR / "sources.yaml").read_text().lower()
    assert "verify" not in text and "insecure" not in text
    cfg = SourcesConfig.model_validate(load_config("sources").model_dump())
    assert cfg.primary.origin == "official"
    assert all(f.origin in ("archive", "other") for f in cfg.fallbacks)  # fallbacks are labelled
