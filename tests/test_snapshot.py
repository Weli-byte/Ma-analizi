"""S13 snapshot engine (ADR 0027). Data is REAL (the committed 1140-match EPL fixture); the only
injected input is the clock, which is a function argument, so stage windows are testable without
waiting for a real fixture. Models are fit on the TRAIN season only and the test fixture comes
from the VALIDATION season, so nothing leaks. No provider responses are involved."""

import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci_real_data_sanity import AS_OF, FIXTURE_ROOT  # noqa: E402
from src.config import config_dir_for, load_config  # noqa: E402
from src.data.dataset import resolve_dataset  # noqa: E402
from src.data.pipeline import run_pipeline  # noqa: E402
from src.evaluation.context import EvalMode, make_context  # noqa: E402
from src.evaluation.dataset import load_rows  # noqa: E402
from src.features.artifact import load_features  # noqa: E402
from src.features.builder import build_features, load_matches  # noqa: E402
from src.features.history import MatchHistory  # noqa: E402
from src.llm.forecast import UpcomingRow  # noqa: E402
from src.llm.prompt import build_user_prompt, prompt_meta  # noqa: E402
from src.llm.snapshot import build_snapshot  # noqa: E402
from src.models import build_models  # noqa: E402
from src.snapshot.delta import probability_delta  # noqa: E402
from src.snapshot.engine import build_stage_snapshot  # noqa: E402
from src.snapshot.pipeline import run_stage  # noqa: E402
from src.snapshot.stages import (  # noqa: E402
    STAGE_ORDER,
    STAGE_TOLERANCE_MINUTES,
    SnapshotStage,
    StageState,
    cutoff_for_stage,
    due_stage,
    stage_state,
)
from src.snapshot.store import StageLocked, StageStore  # noqa: E402

K = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)


# ------------------------------------------------------------------------------ stage windows
def test_every_stage_cutoff_is_before_kickoff_and_windows_never_overlap():
    cutoffs = [cutoff_for_stage(K, s) for s in STAGE_ORDER]
    assert all(c < K for c in cutoffs) and cutoffs == sorted(cutoffs)
    windows = [
        (c, c + timedelta(minutes=STAGE_TOLERANCE_MINUTES[s]))
        for c, s in zip(cutoffs, STAGE_ORDER, strict=True)
    ]
    for (_, end), (start, _) in zip(windows, windows[1:], strict=False):
        assert end < start  # a given instant belongs to at most one stage


def test_stage_state_and_due_stage():
    c = cutoff_for_stage(K, SnapshotStage.T_90M)
    assert stage_state(K, SnapshotStage.T_90M, c - timedelta(minutes=1)) == StageState.NOT_YET
    assert stage_state(K, SnapshotStage.T_90M, c) == StageState.DUE
    assert stage_state(K, SnapshotStage.T_90M, c + timedelta(minutes=19)) == StageState.DUE
    assert stage_state(K, SnapshotStage.T_90M, c + timedelta(minutes=21)) == StageState.MISSED
    assert due_stage(K, c + timedelta(minutes=5)) == SnapshotStage.T_90M
    assert due_stage(K, K - timedelta(hours=5)) is None  # between windows: nothing due


def test_post_kickoff_every_stage_is_missed():
    for s in STAGE_ORDER:
        assert stage_state(K, s, K) == StageState.MISSED
        assert stage_state(K, s, K + timedelta(minutes=1)) == StageState.MISSED
    assert due_stage(K, K + timedelta(seconds=1)) is None


# --------------------------------------------------------------------------------- real data run
@pytest.fixture(scope="module")
def real_project(tmp_path_factory):
    root = tmp_path_factory.mktemp("snap") / "proj"
    shutil.copytree(FIXTURE_ROOT, root, ignore=shutil.ignore_patterns("artifacts", "__pycache__"))
    run_pipeline(root, "research", as_of=AS_OF)
    build_features(root, "research", audit_samples=20)
    cdir = config_dir_for(root)
    data_cfg, model_cfg, eval_cfg = (load_config(n, cdir) for n in ("data", "model", "evaluation"))
    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)
    ctx = make_context(EvalMode.VALIDATION, eval_cfg)
    train = load_rows(ref, ctx, list(eval_cfg.train_seasons), feats)  # TRAIN season only
    models = build_models(["historical_prior", "elo", "poisson"], model_cfg.elo, model_cfg.poisson)
    for m in models:
        m.fit(train)
    matches = load_matches(ref)
    val_match = next(m for m in reversed(matches) if m.season == eval_cfg.validation_seasons[0])
    fixture = UpcomingRow(
        val_match.fixture_id,
        "EPL",
        val_match.season,
        val_match.kickoff_utc,
        val_match.home_id,
        val_match.away_id,
    )
    return {
        "root": root, "ref": ref, "models": models, "history": MatchHistory(matches),
        "fixture": fixture, "feature_version": model_cfg.feature_version,
        "feat_cfg": load_config("features", cdir),
    }  # fmt: skip


def stage_run(rp, stage, now, store=None):
    return run_stage(
        store or StageStore(rp["root"]), rp["fixture"], rp["history"], stage, now,
        rp["ref"].data_version, rp["feature_version"], rp["models"], rp["feat_cfg"],
    )  # fmt: skip


def test_snapshot_hash_is_content_derived_and_deterministic(real_project):
    rp = real_project
    a = build_stage_snapshot(rp["fixture"], rp["history"], SnapshotStage.T_24H, rp["ref"].data_version,
                             rp["feature_version"], rp["feat_cfg"])  # fmt: skip
    b = build_stage_snapshot(rp["fixture"], rp["history"], SnapshotStage.T_24H, rp["ref"].data_version,
                             rp["feature_version"], rp["feat_cfg"])  # fmt: skip
    c = build_stage_snapshot(rp["fixture"], rp["history"], SnapshotStage.T_30M, rp["ref"].data_version,
                             rp["feature_version"], rp["feat_cfg"])  # fmt: skip
    assert a.snapshot_hash == b.snapshot_hash and a.snapshot_hash != c.snapshot_hash
    assert a.information_cutoff == cutoff_for_stage(rp["fixture"].kickoff_utc, SnapshotStage.T_24H)
    # Phase 21: same snapshot + same prompt version => the same system-side request payload
    ma = prompt_meta(build_user_prompt(build_snapshot(a.to_eval_row(), a.information_cutoff)))
    mb = prompt_meta(build_user_prompt(build_snapshot(b.to_eval_row(), b.information_cutoff)))
    assert ma == mb


def test_lineups_and_injuries_are_unknown_never_invented(real_project):
    rp = real_project
    snap = build_stage_snapshot(rp["fixture"], rp["history"], SnapshotStage.T_90M, rp["ref"].data_version,
                                rp["feature_version"], rp["feat_cfg"])  # fmt: skip
    for key in ("lineups", "injuries"):
        assert snap.availability[key] == {"status": "UNKNOWN", "reason": "no_provider"}
    assert not any("lineup" in k or "injur" in k for k in snap.features)
    assert snap.to_eval_row().outcome == -1 and not snap.to_eval_row().odds  # no result, no odds


def test_stage_runs_when_due_writes_locked_artifacts_and_is_immutable(real_project):
    rp = real_project
    k = rp["fixture"].kickoff_utc
    store = StageStore(rp["root"])
    now = cutoff_for_stage(k, SnapshotStage.T_24H) + timedelta(minutes=7)
    res = stage_run(rp, SnapshotStage.T_24H, now, store)
    assert res.status == "COMPLETED" and res.lateness_minutes == 7.0
    assert {p.model_id for p in res.predictions} == {"historical_prior", "elo", "poisson"}
    assert all(p.generated_at == now and p.information_cutoff < p.kickoff_utc for p in res.predictions)
    assert store.is_locked(rp["fixture"].fixture_id, SnapshotStage.T_24H)

    # re-invoking later (even inside the window) returns the STORED forecast, never recomputes
    again = stage_run(rp, SnapshotStage.T_24H, now + timedelta(minutes=30), store)
    assert again.status == "ALREADY_LOCKED" and again.snapshot_hash == res.snapshot_hash
    assert {p.model_id: p.prediction_id for p in again.predictions} == {
        p.model_id: p.prediction_id for p in res.predictions
    }
    with pytest.raises(StageLocked):
        store.write_stage(rp["fixture"].fixture_id, SnapshotStage.T_24H, {"snapshot.json": "{}"}, {})


def test_next_stage_records_delta_against_previous_locked_stage(real_project):
    rp = real_project
    k = rp["fixture"].kickoff_utc
    store = StageStore(rp["root"])
    stage_run(rp, SnapshotStage.T_24H, cutoff_for_stage(k, SnapshotStage.T_24H) + timedelta(minutes=1), store)
    res = stage_run(
        rp, SnapshotStage.T_90M, cutoff_for_stage(k, SnapshotStage.T_90M) + timedelta(minutes=2), store
    )
    assert res.status == "COMPLETED" and len(res.deltas) == 3
    for d in res.deltas:
        assert d["from_stage"] == "t-24h" and d["to_stage"] == "t-90m"
        assert d["max_abs_delta"] >= 0.0
    prev = {p.model_id: p for p in store.read(rp["fixture"].fixture_id, SnapshotStage.T_24H).predictions}
    cur = {p.model_id: p for p in res.predictions}
    expected = probability_delta(prev["elo"], cur["elo"], "t-90m", "t-24h")
    assert next(d for d in res.deltas if d["model_id"] == "elo")["delta_home"] == expected.delta_home


def test_missed_and_not_yet_stages_write_nothing(real_project):
    rp = real_project
    k = rp["fixture"].kickoff_utc
    store = StageStore(rp["root"])
    late = cutoff_for_stage(k, SnapshotStage.T_30M) + timedelta(minutes=11)  # window is 10 min
    assert stage_run(rp, SnapshotStage.T_30M, late, store).status == "MISSED"
    early = cutoff_for_stage(k, SnapshotStage.KICKOFF) - timedelta(minutes=2)
    assert stage_run(rp, SnapshotStage.KICKOFF, early, store).status == "NOT_YET"
    assert stage_run(rp, SnapshotStage.KICKOFF, k + timedelta(seconds=1), store).status == "MISSED"
    assert not store.is_locked(rp["fixture"].fixture_id, SnapshotStage.T_30M)
    assert not store.is_locked(rp["fixture"].fixture_id, SnapshotStage.KICKOFF)


def test_snapshot_cli_modules_import():
    import importlib

    assert callable(importlib.import_module("src.snapshot.run").main)


def test_dashboard_lists_locked_stage_predictions_and_deltas(real_project):
    from src.dashboard.render import render_html
    from src.dashboard.viewmodel import build_viewmodel

    rp = real_project
    for cfg in (Path(__file__).resolve().parents[1] / "configs").glob("*.yaml"):  # ops sections need these
        if not (rp["root"] / "configs" / cfg.name).exists():
            shutil.copy(cfg, rp["root"] / "configs" / cfg.name)
    k = rp["fixture"].kickoff_utc
    store = StageStore(rp["root"])
    stage_run(rp, SnapshotStage.T_24H, cutoff_for_stage(k, SnapshotStage.T_24H) + timedelta(minutes=1), store)
    stage_run(rp, SnapshotStage.T_90M, cutoff_for_stage(k, SnapshotStage.T_90M) + timedelta(minutes=2), store)
    vm = build_viewmodel(rp["root"], k - timedelta(days=2))
    (m,) = [x for x in vm["matches"] if x["upcoming"] and "t-24h" in x["stages"]]
    assert set(m["stages"]) >= {"t-24h", "t-90m"} and m["lineups"] == "UNKNOWN"
    stage_preds = [p for p in vm["predictions"] if p["source"].startswith("stage")]
    assert {p["model_id"] for p in stage_preds} >= {"elo", "poisson", "historical_prior"}
    assert all(p["model_class"] != "unknown" for p in stage_preds) and vm["updates"]
    assert "t-90m" in render_html(vm)


def test_public_service_output_never_carries_player_level_injury_data(real_project):
    """API-Football terms give no publication licence (licensing.md): only STATUS labels may be public."""
    import json as _json
    from datetime import UTC as _UTC

    from src.api.service import ForecastService

    rp = real_project
    k = rp["fixture"].kickoff_utc
    store = StageStore(rp["root"])
    stage_run(rp, SnapshotStage.T_24H, cutoff_for_stage(k, SnapshotStage.T_24H) + timedelta(minutes=1), store)
    svc = ForecastService(rp["root"], lambda: (k - timedelta(days=2)).astimezone(_UTC))
    blob = _json.dumps(svc.fixtures(False, None))
    assert '"players"' not in blob and '"availability_pct"' not in blob and '"injuries": "' in blob
