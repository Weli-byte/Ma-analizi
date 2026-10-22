"""Sprint 6 (ADR 0068): shadow run. For every real upcoming fixture whose snapshot stage is DUE now, the production-family global model and every Sprint 5 candidate
that is allowed to shadow are asked for a prediction on the SAME as-of snapshot, and each prediction is stored as an immutable prospective record. Nothing here is shown
to a user and nothing changes what users see.

Data rules: the models are fitted ONLY on licence-clean history (openfootball) finished at least the result lag before `now`; the fixture schedule comes from the
football-data.org free tier (read-only, CONDITIONAL licence, so every record is research evidence and a canary could never serve it); unknown lineups / odds are
simply not features (null in the record); the Sprint 5 calibrators are restored from their recorded parameters. The frozen-holdout fixtures are ordinary history
here: their one evaluation is finished (`s5_holdout_access_log.json`), and fitting an operational model on them does not read the holdout for evaluation."""

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.evaluation.dataset import EvalRow
from src.league_models import ml_benchmark as mb
from src.league_models import stat_benchmark as sb
from src.league_models.benchmark import goal_target_probs
from src.league_models.calibrators import CalibrationError, Calibrator
from src.league_models.competitions import load_config as load_league_config
from src.league_models.coverage import collect
from src.league_models.licenses import load_manifest, training_gate
from src.league_models.ml_features import FeatureConfig, build_features
from src.league_models.ml_models import GBM, full_params, load_ml_config, tune
from src.league_models.splits import HOLDOUT, build_universe, load_bench_config, to_eval_row
from src.league_models.stat_models import NotTrained, RidgePoissonDC, TimeAwareElo
from src.models import build_models
from src.snapshot.stages import SnapshotStage, due_stage

from .control import LeagueModelRegistry, RolloutConfig, load_rollout
from .records import ProspectiveConflict, ProspectiveError, ProspectiveRecord, ProspectiveStore

SOURCE = "football-data-org"
TRAIN_LICENSES = ["openfootball_domestic", "openfootball_cups"]
SCHEDULE_LICENSE = "football_data_org"
ALIAS_FALLBACK_SOURCE = "openfootball"
COUNTRY = {"ENG1": "ENG", "ITA1": "ITA", "FRA1": "FRA", "ESP1": "ESP", "GER1": "GER"}


def season_label(now: datetime) -> str:
    y = now.year if now.month >= 7 else now.year - 1
    return f"{y}-{str(y + 1)[2:]}"


@dataclass
class Cell:
    league: str
    target: str            # 1x2 | over_under | btts
    model: str             # E_elo_bridge | B_gbm_league | B_dc_league


def candidate_cells(registry: LeagueModelRegistry, cfg: RolloutConfig) -> list[Cell]:
    out = []
    for e in registry.candidates(set(cfg["shadow"]["eligible_statuses"])):
        out.append(Cell(e.league, e.target, e.model_id))
    return sorted(out, key=lambda c: (c.league, c.target, c.model))


def operational_history(audit, bcfg, now: datetime, lag_hours: int) -> list[EvalRow]:
    """Every finished licence-clean match that is history at `now` (kick-off + lag <= now). The guard seasons of the older research benchmark stay out."""
    uni = build_universe(bcfg, audit)
    cutoff = now - timedelta(hours=lag_hours)
    rows = []
    for r in audit.identity_rows:
        if r.status != "FINISHED" or r.final_score is None:
            continue
        role = uni.roles.get((r.canonical_league_id, r.season_id))
        if role is None or role in ("BLOCKED", "GUARD"):
            continue
        if r.kickoff_utc <= cutoff:
            rows.append(to_eval_row(r))
    return sorted(rows, key=lambda x: (x.kickoff_utc, x.fixture_id))


@dataclass
class ShadowModels:
    version_seed: str
    elo: object
    dc_prod: object
    cells: list[Cell]
    e_elo: TimeAwareElo | None = None
    b_dc: RidgePoissonDC | None = None
    b_gbm: dict = field(default_factory=dict)         # target -> GBM
    calibrators: dict = field(default_factory=dict)   # (target, key) -> Calibrator | None
    history: list = field(default_factory=list)
    fcfg: FeatureConfig | None = None
    mcfg: object = None
    errors: dict = field(default_factory=dict)        # model -> reason it is unavailable (missing model, not trained)

    def version(self, family: str) -> str:
        return f"{family}-{hashlib.sha256((self.version_seed + family).encode()).hexdigest()[:10]}"


def restore_calibrators(path: Path) -> tuple[dict, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for target, models in data["final_calibrators"].items():
        for key, d in models.items():
            try:
                out[(target, key)] = Calibrator.from_dict(d)
            except CalibrationError:
                out[(target, key)] = None
    return out, hashlib.sha256(path.read_bytes()).hexdigest()


def fit_models(history: list[EvalRow], cells: list[Cell], root: Path, snapshot_id: str, calibration_manifest: Path, scfg, mcfg, lag_hours: int, bcfg) -> ShadowModels:
    model_cfg = load_config("model", config_dir_for(root))
    elo, dcp = build_models(["elo", "dixon_coles"], elo_config=model_cfg.elo, poisson_config=model_cfg.poisson)
    elo.fit(history)
    dcp.fit(history)
    cals, cal_hash = restore_calibrators(calibration_manifest)
    seed = "|".join([snapshot_id, scfg.fingerprint, mcfg.fingerprint, cal_hash, str(bcfg.over_under_line)])
    sm = ShadowModels(seed, elo, dcp, cells, calibrators=cals, history=history, mcfg=mcfg)
    names = {c.model for c in cells}
    if "E_elo_bridge" in names:
        try:
            sm.e_elo = TimeAwareElo(scfg.elo_spec(), "shadow_elo", sb.UEFA_IDS).fit(history)
        except NotTrained as e:
            sm.errors["E_elo_bridge"] = f"NOT_TRAINED: {e}"
    if "B_dc_league" in names:
        mod, d = sb._fit_dc(scfg.dc_spec(competition_terms=True, country_levels=True), "shadow_b_dc", history, None)
        if mod is None:
            sm.errors["B_dc_league"] = f"NOT_TRAINED: {d.get('reason')}"
        sm.b_dc = mod
    if "B_gbm_league" in names:
        sm.fcfg = FeatureConfig.from_raw(mcfg.raw, mcfg.raw["league_vocabulary"], lag_hours, bcfg.default_stage, scfg.elo_spec())
        for target in sorted({c.target for c in cells if c.model == "B_gbm_league"}):
            try:
                sm.b_gbm[target] = _fit_gbm(history, sm.fcfg, mcfg, target, bcfg)
            except Exception as e:  # a model that cannot be fitted is reported as missing, never replaced by a guess
                sm.errors[f"B_gbm_league:{target}"] = f"NOT_TRAINED: {e}"
    return sm


def _fit_gbm(history, fcfg, mcfg, target: str, bcfg) -> GBM:
    from src.league_models.benchmark import outcomes_for

    fm = build_features(history, fcfg)
    X = fm.X
    y = outcomes_for(history, bcfg.over_under_line)[target]
    seasons = np.array([r.season for r in history])
    uniq = sorted(set(seasons), key=lambda s: int(s.split("-")[0]))
    val_season = next(s for s in reversed(uniq) if int((seasons == s).sum()) >= 300)
    val = seasons == val_season
    start = min(r.kickoff_utc for r, v in zip(history, val, strict=True) if v)
    lag = timedelta(hours=3)
    fit = np.array([(s != val_season) and r.kickoff_utc + lag <= start for r, s in zip(history, seasons, strict=True)])
    names = list(fm.schema.names)
    tn = tune(mcfg, target, X[fit], y[fit], X[val], y[val], names, ["league_code"], int(mcfg.raw["seed"]))
    params = full_params(mcfg, tn["best_params"])
    rounds = max(10, round(tn["best_iteration"] * len(history) / int(fit.sum())))
    g = mcfg.raw["gbm"]
    model = GBM("lightgbm", target, params, names, ["league_code"], int(mcfg.raw["seed"]), g["threads"], g["class_weight"]).fit(X, y, rounds)
    model.tuning_summary = {"best_validation_log_loss": tn["best_validation_log_loss"], "trials": tn["trials_run"], "rounds": rounds}
    return model


def _cal(sm: ShadowModels, target: str, key: str, p, league: str, flags: dict):
    cal = sm.calibrators.get((target, key))
    if cal is None:
        flags[f"calibration_{target}"] = "NOT_RESTORABLE_RAW_USED" if (target, key) in sm.calibrators else "NO_CALIBRATOR_RECORDED_RAW_USED"
        return p
    flags[f"calibration_{target}"] = cal.effective_method
    return cal.apply(p, [league] * len(p) if getattr(p, "ndim", 1) == 1 else [league] * len(p))


def _upcoming_row(fx_id: str, league: str, season: str, kickoff: datetime, home: str, away: str) -> EvalRow:
    return EvalRow(fx_id, league, season, kickoff, home, away, 1, home_goals=None, away_goals=None)  # NO outcome: the placeholder is never read by a prediction


def predict_global(sm: ShadowModels, row: EvalRow, line: float) -> tuple[dict, dict]:
    """Production-family global model: Elo for 1X2, Dixon-Coles for over/under and BTTS, calibrated with the Sprint 5 calibrators."""
    flags: dict = {}
    elo = copy.deepcopy(sm.elo)  # the Elo advances its state while predicting: a fresh copy per fixture keeps every prediction independent
    p = np.asarray(elo.predict_proba([row]), dtype=float)
    p = _cal(sm, "1x2", "elo_s2", p, row.league_id, flags)
    over, both = goal_target_probs(sm.dc_prod, [row], line)
    over = _cal(sm, "over_under", "dixon_coles_totals", np.asarray(over), row.league_id, flags)
    both = _cal(sm, "btts", "dixon_coles_goals", np.asarray(both), row.league_id, flags)
    return {"p_home": float(p[0, 0]), "p_draw": float(p[0, 1]), "p_away": float(p[0, 2]), "p_over25": float(over[0]), "p_btts_yes": float(both[0])}, flags


def predict_cell(sm: ShadowModels, cell: Cell, row: EvalRow, line: float) -> tuple[dict | None, dict, str | None]:
    """One candidate for its own target only; every other probability is null. Returns (probabilities, flags, fallback_reason)."""
    flags: dict = {}
    fields = dict.fromkeys(("p_home", "p_draw", "p_away", "p_over25", "p_btts_yes"))
    if cell.model == "E_elo_bridge":
        if sm.e_elo is None:
            return None, flags, sm.errors.get("E_elo_bridge", "MODEL_MISSING")
        diffs, unc = sm.e_elo.replay([row], update=False)
        x = diffs / 400.0
        from src.league_models.stat_models import _sigmoid

        s1, s2 = _sigmoid(sm.e_elo._t1 - sm.e_elo._beta * x), _sigmoid(sm.e_elo._t2 - sm.e_elo._beta * x)
        p = np.stack([1.0 - s2, np.clip(s2 - s1, 0.0, None), s1], axis=1)
        p = _cal(sm, "1x2", "E_elo_bridge", p / p.sum(axis=1, keepdims=True), row.league_id, flags)
        flags["uncertain_club_history"] = bool(unc[0])
        fields.update(p_home=float(p[0, 0]), p_draw=float(p[0, 1]), p_away=float(p[0, 2]))
    elif cell.model == "B_dc_league":
        if sm.b_dc is None:
            return None, flags, sm.errors.get("B_dc_league", "MODEL_MISSING")
        pred = sm.b_dc.predict([row])
        if cell.target == "over_under":
            q = _cal(sm, "over_under", "B_dc_league_totals", np.clip(pred.over(line), 0, 1), row.league_id, flags)
            fields["p_over25"] = float(q[0])
        elif cell.target == "btts":
            q = _cal(sm, "btts", "B_dc_league_goals", np.clip(pred.btts(), 0, 1), row.league_id, flags)
            fields["p_btts_yes"] = float(q[0])
        else:
            p = pred.one_x_two()
            p = _cal(sm, "1x2", "B_dc_league", p / p.sum(axis=1, keepdims=True), row.league_id, flags)
            fields.update(p_home=float(p[0, 0]), p_draw=float(p[0, 1]), p_away=float(p[0, 2]))
        flags["unseen_club"] = bool(not (pred.seen_home[0] and pred.seen_away[0]))
    elif cell.model == "B_gbm_league":
        gbm = sm.b_gbm.get(cell.target)
        if gbm is None:
            return None, flags, sm.errors.get(f"B_gbm_league:{cell.target}", "MODEL_MISSING")
        fm = build_features([*sm.history, row], sm.fcfg)
        x = fm.X[-1:]
        p = gbm.predict_proba(x)
        if cell.target == "1x2":
            p = _cal(sm, "1x2", "B_gbm_league", p, row.league_id, flags)
            fields.update(p_home=float(p[0, 0]), p_draw=float(p[0, 1]), p_away=float(p[0, 2]))
        flags["form_available"] = bool(x[0, fm.schema.index("form_available")] == 1.0)
        flags["lineups"] = "UNKNOWN_NOT_A_FEATURE"
    else:
        return None, flags, f"UNKNOWN_MODEL:{cell.model}"
    return fields, flags, None


def run_shadow(root: Path, now: datetime, *, provider=None, audit=None, cfg: RolloutConfig | None = None, registry: LeagueModelRegistry | None = None, out_dir: Path | None = None,
               calibration_manifest: Path | None = None, directory: TeamDirectory | None = None, bench_config_path=None, stat_config_path=None, ml_config_path=None,
               stages: list[str] | None = None) -> dict:
    """Ingest -> canonical mapping -> as-of history -> global + candidate -> immutable prospective records. Returns the run report (also written)."""
    t_start = time.perf_counter()
    cfg = cfg or load_rollout()
    out_dir = out_dir or (root / "artifacts" / "league_models" / "rollout")
    report: dict = {"run_at_utc": now.isoformat(), "config_fingerprint": cfg.fingerprint, "status": "OK", "provider_errors": [], "skipped": [], "records": {"CREATED": 0, "EXISTS": 0, "CONFLICT": 0, "REFUSED": 0},
                    "fixtures_seen": 0, "fixtures_due": 0, "fixtures_mapped": 0, "per_fixture_ms": {}}  # fmt: skip
    if not cfg["shadow"]["enabled"]:
        return {**report, "status": "SHADOW_DISABLED"}
    registry = registry or LeagueModelRegistry.load(root=root / cfg["registry_artifacts_root"])
    cells = candidate_cells(registry, cfg)
    manifest = load_manifest()
    training_gate(manifest, TRAIN_LICENSES, "production_ml")  # fail closed before any row is read
    schedule_lic = manifest.get(SCHEDULE_LICENSE)
    license_ids = (*TRAIN_LICENSES[:1], SCHEDULE_LICENSE)
    report["schedule_license_plan_status"] = None if schedule_lic is None else schedule_lic.plan_status

    league_cfg = load_league_config()
    bcfg = load_bench_config(bench_config_path) if bench_config_path else load_bench_config()
    scfg = sb.load_stat_config(stat_config_path) if stat_config_path else sb.load_stat_config()
    mcfg = load_ml_config(ml_config_path) if ml_config_path else load_ml_config()
    directory = directory or TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    if audit is None:
        audit = collect(root, league_cfg, directory, manifest, now)
    snapshot_id = "snap-" + hashlib.sha256("".join(f.sha256 for f in audit.source_files).encode()).hexdigest()[:12]
    report["snapshot_id"] = snapshot_id

    # 1. ingest the schedule (read-only). A provider failure produces ZERO records and an auditable error, never a guess.
    if provider is None:
        from src.cli_utils import load_dotenv
        from src.ingestion.football_data_org import FootballDataOrgProvider

        load_dotenv()
        ing = load_config("ingestion", config_dir_for(root))
        key = ing.api_key(SOURCE)
        if not key:
            return {**report, "status": "NOT_CONFIGURED", "reason": "FOOTBALL_DATA_ORG_API_KEY is not set: the schedule cannot be read"}
        provider = FootballDataOrgProvider(key)
    season = season_label(now)
    upcoming = []
    for code, league in cfg["shadow"]["providers_competitions"].items():
        try:
            raws = provider.list_fixtures(code, season)
        except Exception as e:
            report["provider_errors"].append({"competition": code, "error": type(e).__name__, "message": str(e)[:200]})
            continue
        report["fixtures_seen"] += len(raws)
        for raw in raws:
            if raw.status_raw == "NS" and raw.kickoff_utc > now:
                stage = due_stage(raw.kickoff_utc, now)
                if stage is not None and stage.value in (stages or cfg["shadow"]["stages"]):
                    upcoming.append((league, raw, stage))
    report["fixtures_due"] = len(upcoming)
    if report["provider_errors"] and not upcoming:
        report["status"] = "PROVIDER_FAILED"
    if not upcoming:
        return _write_report(out_dir, report, t_start)

    # 2. canonical mapping (never auto-registers a club)
    rows = []
    id_flags: dict[str, str] = {}
    for league, raw, stage in upcoming:
        day = raw.kickoff_utc.date()
        country = COUNTRY.get(league, league[:3])
        h = directory.resolve(SOURCE, raw.home_team_raw_name, country, day)
        a = directory.resolve(SOURCE, raw.away_team_raw_name, country, day)
        used_fallback = False
        if h.team_id is None:  # same string, already approved under the licence-clean source: an exact alias, never a fuzzy guess and never a new registration
            h, used_fallback = directory.resolve(ALIAS_FALLBACK_SOURCE, raw.home_team_raw_name, country, day), True
        if a.team_id is None:
            a, used_fallback = directory.resolve(ALIAS_FALLBACK_SOURCE, raw.away_team_raw_name, country, day), True
        if h.team_id is None or a.team_id is None:
            report["skipped"].append({"fixture": raw.provider_fixture_id, "reason": "UNRESOLVED_TEAM", "names": [raw.home_team_raw_name, raw.away_team_raw_name]})
            continue
        rows.append((stage, _upcoming_row(f"fdorg-{raw.provider_fixture_id}", league, raw.season, raw.kickoff_utc, h.team_id, a.team_id)))
        if used_fallback:
            id_flags[f"fdorg-{raw.provider_fixture_id}"] = "EXACT_ALIAS_FROM_OPENFOOTBALL_NAMESPACE"
    report["fixtures_mapped"] = len(rows)
    duplicates = len(rows) - len({r.fixture_id for _, r in rows})
    if duplicates:
        report["status"] = "DUPLICATE_FIXTURE_IDS"
        report["skipped"].append({"reason": "DUPLICATE_FIXTURE_IDS", "count": duplicates})
        return _write_report(out_dir, report, t_start)
    if not rows:
        return _write_report(out_dir, report, t_start)

    # 3. as-of history and models
    history = operational_history(audit, bcfg, now, league_cfg.result_lag_hours)
    report["history_rows"] = len(history)
    report["history_latest_kickoff_utc"] = max(r.kickoff_utc for r in history).isoformat()
    cal_path = calibration_manifest or (root / "docs" / "league_models" / "s5_calibration_manifest.json")
    sm = fit_models(history, cells, root, snapshot_id, cal_path, scfg, mcfg, league_cfg.result_lag_hours, bcfg)
    report["model_errors"] = sm.errors
    store = ProspectiveStore(out_dir / "prospective.jsonl")
    line = float(cfg["shadow"]["line"])
    hist_digest = hashlib.sha256(f"{len(history)}|{report['history_latest_kickoff_utc']}|{snapshot_id}".encode()).hexdigest()

    # 4. predict + store
    for stage, row in rows:
        t0 = time.perf_counter()
        snap = hashlib.sha256(json.dumps({"history": hist_digest, "fixture": row.fixture_id, "home": row.home_id, "away": row.away_id, "kickoff": row.kickoff_utc.isoformat(), "stage": stage.value},
                                         sort_keys=True).encode()).hexdigest()
        jobs = [("global", "global", predict_global(sm, row, line) + (None,))]
        for cell in [c for c in sm.cells if c.league == row.league_id]:
            fields, flags, why = predict_cell(sm, cell, row, line)
            jobs.append((cell.model + ":" + cell.target, "candidate", (fields, flags, why)))
        for family, role, (fields, flags, why) in jobs:
            fam = family if role == "global" else family.split(":")[0]
            if fields is None:
                report["skipped"].append({"fixture": row.fixture_id, "family": family, "reason": why or "NO_PREDICTION"})
                continue
            rec = ProspectiveRecord(row.fixture_id, row.league_id, row.kickoff_utc.isoformat(), now.isoformat(), stage.value, sm.version(fam), fam if role == "global" else family, role,
                                    fields["p_home"], fields["p_draw"], fields["p_away"], fields["p_over25"], fields["p_btts_yes"], snap, license_ids,
                                    {**flags, "lineups": "UNKNOWN", "odds": "NOT_USED", **({"identity": id_flags[row.fixture_id]} if row.fixture_id in id_flags else {})}, why)  # fmt: skip
            try:
                report["records"][store.put(rec, now)] += 1
            except ProspectiveConflict as e:
                report["records"]["CONFLICT"] += 1
                report["skipped"].append({"fixture": row.fixture_id, "family": family, "reason": f"CONFLICT:{e}"[:160]})
            except ProspectiveError as e:
                report["records"]["REFUSED"] += 1
                report["skipped"].append({"fixture": row.fixture_id, "family": family, "reason": f"REFUSED:{e}"[:160]})
        report["per_fixture_ms"][row.fixture_id] = round((time.perf_counter() - t0) * 1000, 1)
    if report["records"]["CONFLICT"] or report["records"]["REFUSED"]:
        report["status"] = "RECORDS_REFUSED"
    return _write_report(out_dir, report, t_start)


def _write_report(out_dir: Path, report: dict, t_start: float) -> dict:
    report["wall_seconds"] = round(time.perf_counter() - t_start, 2)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "shadow_runs.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, sort_keys=True, default=str) + "\n")
    return report


__all__ = ["Cell", "ShadowModels", "run_shadow", "candidate_cells", "operational_history", "fit_models", "predict_global", "predict_cell", "SnapshotStage", "HOLDOUT", "mb"]
