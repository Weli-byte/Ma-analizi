"""Offline paths of the operational CLIs (no network, no provider call): configuration failures,
NOT_CONFIGURED reporting, budget refusal before any call, model fitting and live pre-match rates."""

import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci_real_data_sanity import AS_OF, FIXTURE_ROOT  # noqa: E402
from src.config import ProviderConfig, config_dir_for, load_config  # noqa: E402
from src.data.pipeline import run_pipeline  # noqa: E402
from src.data.teams import TeamDirectory  # noqa: E402
from src.features.builder import build_features  # noqa: E402
from src.live import run as live_run  # noqa: E402
from src.llm import live_smoke  # noqa: E402
from src.llm.budget import BudgetExceeded  # noqa: E402
from src.llm.pricing import load_price_table  # noqa: E402
from src.llm.runner import ProviderNotConfigured  # noqa: E402
from src.snapshot.run import fit_models, make_llm_step  # noqa: E402


@pytest.fixture(scope="module")
def real_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("ops") / "proj"
    shutil.copytree(FIXTURE_ROOT, root, ignore=shutil.ignore_patterns("artifacts", "__pycache__"))
    run_pipeline(root, "research", as_of=AS_OF)
    build_features(root, "research", audit_samples=20)
    return root


def write_provider_yaml(root: Path, body: str) -> None:
    (root / "configs" / "provider.yaml").write_text(body, encoding="utf-8")


def test_live_smoke_reports_not_configured_and_never_passes_without_keys(real_root, monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY_A", raising=False)
    write_provider_yaml(
        real_root,
        "providers:\n  openai: {enabled: true, api_key_env: NO_SUCH_KEY_A, model: gpt-6-luna}\n",
    )
    rows, ready = live_smoke.run_smoke(real_root, capture=False)
    assert ready is False and rows[0]["status"] == "NOT_CONFIGURED"
    assert live_smoke.main(["--root", str(real_root)]) == 1


def test_live_smoke_refuses_over_budget_before_any_call(real_root, monkeypatch):
    monkeypatch.setenv("KEY_B1", "present-but-never-used")
    monkeypatch.setenv("KEY_B2", "present-but-never-used")
    write_provider_yaml(
        real_root,
        "budget: {max_requests_per_run: 1}\nproviders:\n"
        "  openai: {enabled: true, api_key_env: KEY_B1, model: gpt-6-luna}\n"
        "  groq: {enabled: true, api_key_env: KEY_B2, model: openai/gpt-oss-20b}\n",
    )
    with pytest.raises(BudgetExceeded):
        live_smoke.run_smoke(real_root, capture=False)
    assert live_smoke.main(["--root", str(real_root)]) == 3  # no API call was made


def test_snapshot_llm_step_needs_a_configured_provider():
    cfg = ProviderConfig(
        providers={"openai": {"enabled": True, "api_key_env": "NO_SUCH_KEY_C", "model": "m"}}
    )
    with pytest.raises(ProviderNotConfigured):
        make_llm_step(cfg, load_price_table(), cfg.budget, "fv2", "dv-0", 1)


def test_fit_models_uses_train_and_validation_seasons_only(real_root):
    models, info = fit_models(real_root, ["historical_prior", "poisson"])
    eval_cfg = load_config("evaluation", config_dir_for(real_root))
    assert info["seasons"] == list(eval_cfg.train_seasons) + list(eval_cfg.validation_seasons)
    assert not set(info["seasons"]) & set(eval_cfg.final_test_seasons)  # final-test stays locked
    assert info["fit_rows"] > 0 and {m.model_id for m in models} == {"historical_prior", "poisson"}


def test_live_prematch_rates_come_from_the_fitted_poisson_and_flag_unseen_teams(real_root):
    models, _ = fit_models(real_root, ["poisson"])
    directory = TeamDirectory.load(REPO_ROOT / "configs" / "team_aliases.yaml")
    rates = live_run.rates_for(models[0], directory, "PL", "Arsenal FC", "Chelsea FC", date(2026, 10, 10))
    assert rates is not None and rates.home > 0 and rates.away > 0 and "poisson" in rates.source
    assert (
        live_run.rates_for(models[0], directory, "PL", "Nowhere FC", "Chelsea FC", date(2026, 10, 10)) is None
    )


def test_live_runner_rejects_bad_arguments_without_touching_the_network(real_root, capsys):
    assert (
        live_run.main(["--root", str(real_root), "--source", "openligadb", "--league", "bl1", "--loop", "5"])
        == 2
    )
    assert "30 seconds" in capsys.readouterr().err
    assert live_run.main(["--root", str(real_root), "--source", "fdorg", "--league", "XX"]) == 2


def test_benchmark_reports_missing_providers_instead_of_calling_anything(real_root, monkeypatch):
    from src.llm.benchmark import run_benchmark

    monkeypatch.delenv("NO_SUCH_KEY_D", raising=False)
    write_provider_yaml(
        real_root,
        "providers:\n  openai: {enabled: true, api_key_env: NO_SUCH_KEY_D, model: gpt-6-luna}\n",
    )
    with pytest.raises(ProviderNotConfigured, match="no runnable provider"):
        run_benchmark(
            real_root, "historical", None, 2, "PL", "validation", None, None, None, "llm-prompt-v2", 0.0, True
        )
    with pytest.raises(ProviderNotConfigured, match="unknown provider"):
        run_benchmark(
            real_root,
            "historical",
            ["nope"],
            2,
            "PL",
            "validation",
            None,
            None,
            None,
            "llm-prompt-v2",
            0.0,
            True,
        )
    with pytest.raises(ValueError, match="track"):
        run_benchmark(
            real_root, "weird", None, 2, "PL", "validation", None, None, None, "llm-prompt-v2", 0.0, True
        )
