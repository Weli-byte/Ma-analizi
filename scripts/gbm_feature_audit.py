"""S0-S7 hardening Phase 8 — GBM feature audit (audit findings M-08/M-09).

    python scripts/gbm_feature_audit.py [--root DIR]

For every feature `src.models.gbm.FEATURES` (= `produced_names()`, the S2 leakage-safe
contract) reports: name, type, availability timestamp, source, leakage status, feature
version — all from `src.features.registry` (the contract, static) — plus null rate and
variance computed from the CURRENT built feature artifact (runtime, if one exists for the
configured data/feature version; the report says so plainly if it doesn't rather than
fabricating numbers). Features are grouped the way the hardening task asks for: form,
scoring, conceding, home/away, opponent strength, rest, streaks, future-planned (declared but
inactive, e.g. xG).

Also reports the correlation matrix within the `form_points_{3,5,10}` family (nested windows
of the same underlying signal — by construction correlated) as the Phase 8 "correlated
features" diagnostic (M-09). This is diagnostic-only: no feature is removed by this script.
"""

import argparse
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.features.artifact import StaleArtifactError, load_features
from src.features.registry import EXPERIMENTAL, FEATURE_VERSION, TEAM_FEATURES, VENUE_FEATURES, spec_for
from src.models.gbm import FEATURES

ROOT = Path(__file__).resolve().parents[1]

CATEGORIES: dict[str, tuple[str, ...]] = {
    "form": ("form_points_3", "form_points_5", "form_points_10"),
    "scoring": ("goals_for_avg_5",),
    "conceding": ("goals_against_avg_5",),
    "home/away": ("home_win_rate", "away_win_rate"),
    "opponent strength": ("opp_ppg_5",),
    "rest": ("rest_days_raw", "rest_days_capped", "season_break_flag"),
    "streaks": ("win_streak", "loss_streak"),
}
FUTURE_PLANNED = tuple(s.name for s in EXPERIMENTAL)  # declared, never produced (ADR 0009)


def _category_for(base_name: str) -> str:
    for cat, names in CATEGORIES.items():
        if base_name in names:
            return cat
    return "uncategorized"


def _base_name(produced: str) -> str:
    if produced in VENUE_FEATURES:  # home_win_rate/away_win_rate: already side-specific, keep whole
        return produced
    for side in ("home_", "away_"):
        if produced.startswith(side):
            return produced[len(side):]
    return produced


def _runtime_stats(root: Path) -> dict[str, dict] | None:
    try:
        cdir = config_dir_for(root)
        data_cfg = load_config("data", cdir)
        model_cfg = load_config("model", cdir)
        ref = resolve_dataset(root / data_cfg.processed_dir)
        feats = load_features(root, ref, model_cfg.feature_version)
    except (StaleArtifactError, FileNotFoundError, Exception):  # noqa: BLE001 -- best-effort only
        return None
    values: dict[str, list[float]] = {f: [] for f in FEATURES}
    total = len(feats.rows)
    for fixture_values in feats.rows.values():
        for f in FEATURES:
            v = fixture_values.get(f)
            if v is not None:
                values[f].append(v)
    stats = {}
    for f in FEATURES:
        present = values[f]
        stats[f] = {
            "null_rate": round(1.0 - len(present) / total, 4) if total else None,
            "variance": round(float(np.var(present)), 6) if len(present) > 1 else None,
            "n_present": len(present),
        }
    return stats


def _form_correlation(root: Path) -> str:
    try:
        cdir = config_dir_for(root)
        data_cfg = load_config("data", cdir)
        model_cfg = load_config("model", cdir)
        ref = resolve_dataset(root / data_cfg.processed_dir)
        feats = load_features(root, ref, model_cfg.feature_version)
    except Exception:  # noqa: BLE001 -- best-effort only
        return "(no built feature artifact found; run python -m src.features.builder first)"
    cols = ["home_form_points_3", "home_form_points_5", "home_form_points_10"]
    data = {c: [] for c in cols}
    for fixture_values in feats.rows.values():
        if all(fixture_values.get(c) is not None for c in cols):
            for c in cols:
                data[c].append(fixture_values[c])
    if len(next(iter(data.values()))) < 2:
        return "(not enough co-observed rows to correlate)"
    mat = np.corrcoef([data[c] for c in cols])
    lines = ["| | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, c in enumerate(cols):
        lines.append("| " + c + " | " + " | ".join(f"{mat[i, j]:.3f}" for j in range(len(cols))) + " |")
    return "\n".join(lines)


def render_report(root: Path) -> str:
    runtime = _runtime_stats(root)
    lines = [
        "# GBM feature audit (S0-S7 hardening Phase 8)",
        "",
        f"Feature contract: `src.features.registry` (`FEATURE_VERSION={FEATURE_VERSION}`).",
        "Runtime stats (null rate, variance) computed from the currently built feature artifact"
        + (" — none found; contract-only columns shown." if runtime is None else "."),
        "",
    ]
    for category, base_names in {**CATEGORIES, "future-planned (inactive)": FUTURE_PLANNED}.items():
        lines += [f"## {category}", "", "| feature | type | available_at | source | leakage status | "
                  "null rate | variance |", "|---|---|---|---|---|---|---|"]  # fmt: skip
        for base in base_names:
            try:
                spec = spec_for(base if base in FUTURE_PLANNED else f"home_{base}")
            except KeyError:
                spec = spec_for(base)
            sides = [base] if base in VENUE_FEATURES or base in FUTURE_PLANNED else [
                f"home_{base}", f"away_{base}"
            ]
            for produced in sides:
                if produced not in FEATURES and produced not in FUTURE_PLANNED:
                    continue
                r = runtime.get(produced) if runtime else None
                ftype = "active" if base in TEAM_FEATURES or base in VENUE_FEATURES else spec.status
                lines.append(
                    f"| `{produced}` | {ftype} | {spec.available_at} | {spec.source} | "
                    f"leakage-safe (ADR 0009) | {r['null_rate'] if r else 'n/a'} | "
                    f"{r['variance'] if r else 'n/a'} |"
                )
        lines.append("")
    lines += [
        "## Correlated feature family: form_points_3 / form_points_5 / form_points_10 (M-09)",
        "",
        "By construction correlated (nested rolling windows of the same underlying signal).",
        "Diagnostic only — no feature is auto-removed. Compare full vs a reduced set (drop "
        "form_points_3/form_points_10, keep only form_points_5) under walk-forward via "
        "`scripts/gbm_feature_reduction_comparison.py` before ever considering removal.",
        "",
        _form_correlation(root),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    a = p.parse_args(argv)
    print(render_report(Path(a.root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
