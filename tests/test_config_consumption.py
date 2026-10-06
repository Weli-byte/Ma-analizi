"""No dead configuration: every config field is read by code, or explicitly reserved with a sprint tag."""

import ast
import inspect
from pathlib import Path

import src.config as config_module
from src.config import RESERVED_FIELDS

SRC = Path(__file__).resolve().parents[1] / "src"
CONFIG_FILE = SRC / "config" / "__init__.py"
# fields read dynamically through getattr(cfg, mode.value); verified by a dedicated test below
DYNAMIC = {"FallbackThresholds.development", "FallbackThresholds.research",
           "FallbackThresholds.strict", "FallbackThresholds.final",
           "GBMTrialBudget.development", "GBMTrialBudget.research",
           "GBMTrialBudget.strict", "GBMTrialBudget.final"}  # fmt: skip


def consumed_names() -> set[str]:
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        if path == CONFIG_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    names.add(str(node.args[1].value))
    return names


def config_fields() -> dict[str, str]:
    out = {}
    for name, cls in inspect.getmembers(config_module, inspect.isclass):
        if issubclass(cls, config_module._Cfg) and cls is not config_module._Cfg:
            for field in cls.model_fields:
                out[f"{name}.{field}"] = field
    return out


def test_every_config_field_is_consumed_or_reserved_with_a_sprint_tag():
    used = consumed_names()
    dead = [
        key
        for key, field in config_fields().items()
        if field not in used and key not in RESERVED_FIELDS and key not in DYNAMIC
    ]
    assert not dead, f"declared but never consumed (connect, remove, or reserve): {dead}"


def test_reserved_fields_exist_and_are_tagged():
    fields = config_fields()
    for key, tag in RESERVED_FIELDS.items():
        assert key in fields, f"stale reserved entry {key}"
        assert tag and (tag.startswith("S") or tag == "docs"), f"reserved {key} needs a sprint tag"


def test_dynamic_fallback_thresholds_are_actually_read_by_mode():
    src = (SRC / "evaluation" / "run_baselines.py").read_text(encoding="utf-8")
    assert "getattr(eval_cfg.max_fallback_rate, mode.value)" in src
    modes = {k.split(".")[1] for k in DYNAMIC if k.startswith("FallbackThresholds.")}
    from src.runmode import RunMode

    assert modes == {m.value for m in RunMode}


def test_dynamic_gbm_trial_budget_is_actually_read_by_mode():
    """S0-S7 hardening Phase 8 (audit finding M-10): GBMTrialBudget's 4 fields are read via
    getattr(budget, mode, ...) with a variable `mode`, invisible to the static consumed_names()
    scan -- this test is the dedicated proof they are genuinely read, matching the same pattern
    FallbackThresholds already established above."""
    src = (SRC / "models" / "__init__.py").read_text(encoding="utf-8")
    assert "getattr(budget, mode" in src
    modes = {k.split(".")[1] for k in DYNAMIC if k.startswith("GBMTrialBudget.")}
    from src.runmode import RunMode

    assert modes == {m.value for m in RunMode}


def test_config_files_have_no_unknown_keys_and_yaml_matches_models():
    from src.config import CONFIG_DIR, load_config

    for path in CONFIG_DIR.glob("*.yaml"):
        if path.stem == "team_aliases":
            continue
        assert load_config(path.stem) is not None  # extra=forbid catches stray keys
