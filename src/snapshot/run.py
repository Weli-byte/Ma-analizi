"""S13 live snapshot runner (ADR 0027):

    python -m src.snapshot.run --league PL [--stage auto|t-24h|t-90m|t-30m|kickoff]
                               [--fixture-id ID] [--with-llm]

Meant to be started on a schedule (cron / Task Scheduler, every ~10 minutes). It is cheap when
nothing is due: it only fetches the real fixture list and exits. When a stage window is open
(see `stages.py`) it fits the statistical/ML models once on the train+validation seasons (the
final-test seasons stay locked, ADR 0004), builds the immutable snapshot from REAL data, predicts,
optionally adds REAL LLM predictions (`--with-llm`, needs ALLOW_REAL_LLM_CALLS=true and stays inside
the configured budget), records deltas against the previous stage and LOCKS the stage.
Lineups/injuries are UNKNOWN (no provider yet); nothing is invented. There is no `--now` override:
a stage is run only against the real clock.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.data.teams import TeamDirectory
from src.evaluation.context import EvalMode, make_context
from src.evaluation.dataset import load_rows
from src.features.artifact import load_features
from src.features.builder import load_matches
from src.features.history import MatchHistory
from src.ingestion.fpl import FplInjuryProvider
from src.ingestion.lineups import LEAGUE_CODES as LINEUP_LEAGUES
from src.ingestion.lineups import EspnLineupProvider
from src.ingestion.provider import ProviderError as IngestionProviderError
from src.llm.budget import BudgetExceeded, estimate_input_tokens, preflight
from src.llm.forecast import LEAGUES, load_upcoming_rows
from src.llm.pricing import load_price_table
from src.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from src.llm.providers import PROVIDERS
from src.llm.runner import ProviderNotConfigured, run_one
from src.models import build_models
from src.schemas import ExperimentType

from .availability import failed_block, injuries_block, unknown_block
from .pipeline import run_stage
from .stages import STAGE_ORDER, SnapshotStage, StageState, cutoff_for_stage, due_stage, stage_state
from .store import StageStore

ROOT = Path(__file__).resolve().parents[2]
SKIP_MODELS = {"market_implied"}  # needs exact-timestamp odds, which do not exist (ADR 0005)


def fit_models(root: Path, names: list[str] | None = None, mode: str = "research"):
    """Fit once on TRAIN+VALIDATION seasons only (final-test is locked by EvaluationContext)."""
    cdir = config_dir_for(root)
    data_cfg, model_cfg, eval_cfg = (load_config(n, cdir) for n in ("data", "model", "evaluation"))
    ref = resolve_dataset(root / data_cfg.processed_dir)
    feats = load_features(root, ref, model_cfg.feature_version)
    ctx = make_context(EvalMode.VALIDATION, eval_cfg)
    seasons = list(eval_cfg.train_seasons) + list(eval_cfg.validation_seasons)
    rows = load_rows(ref, ctx, seasons, feats)
    wanted = names or [m for m in (model_cfg.walk_forward_models or model_cfg.models) if m not in SKIP_MODELS]
    models = build_models(wanted, model_cfg.elo, model_cfg.poisson, model_cfg.gbm, mode)
    for m in models:
        m.fit(rows)
    return models, {"fit_rows": len(rows), "seasons": seasons, "models": wanted}


def make_llm_step(cfg, prices, budget, feature_version: str, data_version: str, n_fixtures: int):
    runnable = [
        (n, e) for n, e in cfg.providers.items() if e.enabled and e.model.upper() != "TBD" and cfg.api_key(n)
    ]
    if not runnable:
        raise ProviderNotConfigured("--with-llm: no enabled provider with a key (NOT_CONFIGURED)")
    tok = estimate_input_tokens(SYSTEM_PROMPT, build_user_prompt({"approx": "x" * 4000}))
    preflight([(n, e.model, tok) for n, e in runnable] * n_fixtures, budget, prices)  # before any call

    def step(row, cutoff: datetime):
        preds, calls, resps = [], [], []
        for name, entry in runnable:
            res = run_one(
                row, PROVIDERS[name], entry.model, cfg.api_key(name), ExperimentType.PROSPECTIVE,
                budget, prices, feature_version=feature_version, data_version=data_version,
                information_cutoff=cutoff,
            )  # fmt: skip
            calls.append(res.call.model_dump_json())
            if res.prediction is not None:
                preds.append(res.prediction)
            if res.raw_text is not None:
                resps.append(json.dumps({"provider": name, "model": entry.model,
                                         "request_id": res.call.request_id, "text": res.raw_text},
                                        sort_keys=True))  # fmt: skip
        return preds, calls, resps

    return step


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--league", default="PL", choices=sorted(LEAGUES))
    p.add_argument("--stage", default="auto", choices=["auto", *[s.value for s in STAGE_ORDER]])
    p.add_argument("--fixture-id", default=None)
    p.add_argument("--with-llm", action="store_true")
    p.add_argument("--no-fpl", action="store_true", help="skip the FPL injury source (EPL only)")
    p.add_argument("--no-lineups", action="store_true", help="skip the ESPN lineup source")
    p.add_argument("--models", default=None, help="comma list; default: configured models")
    a = p.parse_args(argv)
    root = Path(a.root)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        bundle = load_upcoming_rows(root, a.league, a.fixture_id, 30)
        if bundle.skipped_unresolved:
            print(
                f"WARNING unresolved team names skipped (never auto-registered): {bundle.skipped_unresolved}"
            )
        todo = []
        for row in bundle.rows:
            stage = due_stage(row.kickoff_utc, now) if a.stage == "auto" else SnapshotStage(a.stage)
            if stage is not None and stage_state(row.kickoff_utc, stage, now) == StageState.DUE:
                todo.append((row, stage))
        if not todo:
            nxt = min(
                (cutoff_for_stage(r.kickoff_utc, SnapshotStage.T_24H) for r in bundle.rows), default=None
            )
            print(
                f"NO STAGE DUE at {now.isoformat()} (earliest t-24h window opens {nxt and nxt.isoformat()})"
            )
            return 0

        cdir = config_dir_for(root)
        cfg = load_config("provider", cdir)
        model_cfg = load_config("model", cdir)
        feat_cfg = load_config("features", cdir)
        prices = load_price_table()
        llm_step = None
        if a.with_llm:
            if os.environ.get("ALLOW_REAL_LLM_CALLS", "").lower() != "true":
                print("REAL_CALLS_DISABLED_BY_OPERATOR: --with-llm needs ALLOW_REAL_LLM_CALLS=true",
                      file=sys.stderr)  # fmt: skip
                return 4
            llm_step = make_llm_step(
                cfg, prices, cfg.budget, bundle.feature_version, bundle.data_version, len(todo)
            )  # fmt: skip

        models, info = fit_models(root, a.models.split(",") if a.models else None)
        ref = resolve_dataset(root / load_config("data", cdir).processed_dir)
        history = MatchHistory(load_matches(ref))
        store = StageStore(root)
        print(
            f"fitted {info['models']} on {info['fit_rows']} rows "
            f"({info['seasons'][0]}..{info['seasons'][-1]})"
        )
        fpl_players, fpl_observed, fpl_sha, fpl_error = None, None, None, None
        if not a.no_fpl and any(r.league_id == "EPL" for r, _ in todo):
            fpl = FplInjuryProvider(
                TeamDirectory.load(cdir / "team_aliases.yaml"), root / "artifacts" / "ingestion" / "fpl"
            )
            try:
                fpl_observed = datetime.now(UTC)
                fpl_players = fpl.list_availability(fpl_observed)
                fpl_sha = fpl.last_raw_sha256
            except IngestionProviderError as e:
                fpl_error = str(e)
                print(f"WARNING fpl injuries FAILED (stage runs with injuries=FAILED): {e}")
        espn = (
            EspnLineupProvider(TeamDirectory.load(cdir / "team_aliases.yaml")) if not a.no_lineups else None
        )
        for row, stage in todo:
            # 1. everything fetched at run time first; 2. THEN the snapshot time, so no fetched item can
            #    post-date the information cutoff (ADR 0027 amendment)
            lineups = None
            if stage == SnapshotStage.T_24H:
                lineups = unknown_block("not_attempted_at_t-24h")
            elif espn is not None and row.league_id in LINEUP_LEAGUES:
                try:
                    eid = espn.find_event_id(row.league_id, row.home_id, row.away_id, row.kickoff_utc)
                    lineups = (
                        espn.fetch_lineups(row.league_id, eid, datetime.now(UTC))
                        if eid
                        else unknown_block("espn_event_not_found")
                    )
                except IngestionProviderError as e:
                    lineups = failed_block("espn", str(e))
            stage_now = datetime.now(UTC)
            injuries = None
            if row.league_id == "EPL" and not a.no_fpl:
                if fpl_players is not None:
                    injuries = injuries_block(
                        fpl_players, (row.home_id, row.away_id), stage_now, fpl_observed, "fpl", fpl_sha
                    )
                else:
                    injuries = failed_block("fpl", fpl_error or "not fetched")
            res = run_stage(
                store, row, history, stage, stage_now, bundle.data_version, bundle.feature_version,
                models, feat_cfg, llm_step, injuries, lineups,
            )  # fmt: skip
            print(json.dumps({
                "fixture": res.fixture_id, "stage": res.stage, "status": res.status,
                "snapshot_hash": res.snapshot_hash, "lateness_min": res.lateness_minutes,
                "models": {s["model_id"]: s["status"] for s in res.model_statuses},
                "n_predictions": len(res.predictions), "n_deltas": len(res.deltas),
                "injuries": (injuries or {}).get("status", "UNKNOWN"),
                "lineups": (lineups or {}).get("status", "UNKNOWN"),
            }, sort_keys=True))  # fmt: skip
        _ = model_cfg
    except (BudgetExceeded, ProviderNotConfigured, ValueError, RuntimeError, KeyError) as e:
        print(f"SNAPSHOT RUN FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
