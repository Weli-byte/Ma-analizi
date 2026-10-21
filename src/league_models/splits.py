"""Sprint 2 (ADR 0064): the common test universe, chronological folds and the frozen-holdout lock.

Rows come from the Sprint 1 identity layer (licence-clean openfootball scores). Roles of a season, per
competition: DEV (usable for folds), HOLDOUT (frozen: its fixtures are listed by id and hash, its scores never
reach a model), RESERVED (running season, kept for prospective checks), GUARD (a season the global research
benchmark locks as FINAL for EPL / La Liga), BLOCKED (not trainable in Sprint 1). A fixture has exactly one role,
so it can never fall into two splits."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from src.evaluation.dataset import EvalRow
from src.snapshot.stages import SnapshotStage, cutoff_for_stage

from .coverage import AuditResult
from .identity import IdentityRow

ROOT = Path(__file__).resolve().parents[2]
BENCH_CONFIG = ROOT / "configs" / "league_models" / "benchmark.yaml"
EVALUATION_CONFIG = ROOT / "configs" / "evaluation.yaml"

DEV, HOLDOUT, RESERVED, GUARD, BLOCKED = "DEV", "HOLDOUT", "RESERVED", "GUARD", "BLOCKED"
TRAINABLE = ("TRAINABLE_COMPLETE", "TRAINABLE_PARTIAL_CURRENT")


class HoldoutAccessError(PermissionError):
    """A model-facing code path asked for frozen-holdout rows."""


@dataclass(frozen=True)
class BenchConfig:
    protocol_version: str
    default_stage: SnapshotStage
    min_train_seasons: int
    min_train_matches: int
    holdout_default: tuple[str, ...]
    holdout_per_competition: dict[str, tuple[str, ...]]
    reserved_seasons: tuple[str, ...]
    guard_competitions: tuple[str, ...]
    guard_seasons: tuple[str, ...]
    bootstrap_samples: int
    bootstrap_seed: int
    bootstrap_alpha: float
    calibration_bins: int
    over_under_line: float
    reference_model: str
    models_1x2: tuple[str, ...]
    models_over_under: tuple[str, ...]
    models_btts: tuple[str, ...]
    fingerprint: str


def load_bench_config(path: Path = BENCH_CONFIG, evaluation_path: Path = EVALUATION_CONFIG) -> BenchConfig:
    raw_text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)
    guard = raw["global_benchmark_guard"]
    if guard["seasons_from"] != "evaluation_config":
        raise ValueError("global_benchmark_guard.seasons_from must be 'evaluation_config'")
    final = yaml.safe_load(evaluation_path.read_text(encoding="utf-8"))["final_test_seasons"]
    boot = raw["bootstrap"]
    if boot["block"] != "iso_week":
        raise ValueError("only the iso_week bootstrap block is implemented")
    return BenchConfig(
        protocol_version=raw["protocol_version"],
        default_stage=SnapshotStage(raw["default_stage"]),
        min_train_seasons=int(raw["min_train_seasons"]),
        min_train_matches=int(raw["min_train_matches"]),
        holdout_default=tuple(raw["holdout"]["default"]),
        holdout_per_competition={k: tuple(v) for k, v in raw["holdout"]["per_competition"].items()},
        reserved_seasons=tuple(raw["reserved_seasons"]),
        guard_competitions=tuple(guard["competitions"]),
        guard_seasons=tuple(final),
        bootstrap_samples=int(boot["samples"]),
        bootstrap_seed=int(boot["seed"]),
        bootstrap_alpha=float(boot["alpha"]),
        calibration_bins=int(raw["calibration_bins"]),
        over_under_line=float(raw["over_under_line"]),
        reference_model=raw["reference_model"],
        models_1x2=tuple(raw["models_1x2"]),
        models_over_under=tuple(raw["models_over_under"]),
        models_btts=tuple(raw["models_btts"]),
        fingerprint=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )


def season_start_year(season: str) -> int:
    return int(season.split("-")[0])


def season_role(cfg: BenchConfig, competition: str, season: str, trainable: bool) -> str:
    """Role of one (competition, season). Order matters: the frozen holdout wins over everything else."""
    if season in cfg.holdout_per_competition.get(competition, cfg.holdout_default):
        return HOLDOUT if trainable else BLOCKED
    if not trainable:
        return BLOCKED
    if season in cfg.reserved_seasons:
        return RESERVED
    if competition in cfg.guard_competitions and season in cfg.guard_seasons:
        return GUARD
    return DEV


def season_roles(cfg: BenchConfig, audit: AuditResult) -> dict[tuple[str, str], str]:
    return {
        (c.canonical_league_id, c.season): season_role(
            cfg, c.canonical_league_id, c.season, c.training_status in TRAINABLE
        )
        for c in audit.coverage
        if c.source_status != "NOT_APPLICABLE"
    }


def to_eval_row(r: IdentityRow) -> EvalRow:
    """Score-only evaluation row. Outcome and goals are POST-match facts: models only see them for matches that
    are already history at the fold cutoff."""
    if r.final_score is None:
        raise ValueError(f"{r.canonical_fixture_id} has no final score")
    hg, ag = r.final_score
    outcome = 0 if hg > ag else (1 if hg == ag else 2)
    return EvalRow(
        r.canonical_fixture_id,
        r.canonical_league_id,
        r.season_id,
        r.kickoff_utc,
        r.canonical_home_team_id,
        r.canonical_away_team_id,
        outcome,
        home_goals=hg,
        away_goals=ag,
    )


@dataclass(frozen=True)
class Universe:
    """Finished fixtures split by role. `holdout` holds only fixture ids: the scores are not carried, so no
    downstream code can read them by accident."""

    dev: tuple[EvalRow, ...]
    holdout_ids: tuple[str, ...]
    reserved_ids: tuple[str, ...]
    guard_ids: tuple[str, ...]
    roles: dict[tuple[str, str], str]
    kickoff_known: dict[str, bool]

    def counts(self) -> dict[str, int]:
        return {
            DEV: len(self.dev),
            HOLDOUT: len(self.holdout_ids),
            RESERVED: len(self.reserved_ids),
            GUARD: len(self.guard_ids),
        }


def build_universe(cfg: BenchConfig, audit: AuditResult) -> Universe:
    roles = season_roles(cfg, audit)
    dev: list[EvalRow] = []
    ids: dict[str, list[str]] = {HOLDOUT: [], RESERVED: [], GUARD: []}
    known: dict[str, bool] = {}
    seen: set[str] = set()
    for r in sorted(audit.identity_rows, key=lambda x: (x.kickoff_utc, x.canonical_fixture_id)):
        if r.status != "FINISHED":
            continue
        role = roles.get((r.canonical_league_id, r.season_id), BLOCKED)
        if role == BLOCKED:
            continue
        if r.canonical_fixture_id in seen:
            raise ValueError(f"duplicate fixture id in the universe: {r.canonical_fixture_id}")
        seen.add(r.canonical_fixture_id)
        known[r.canonical_fixture_id] = r.kickoff_time_known
        if role == DEV:
            dev.append(to_eval_row(r))
        else:
            ids[role].append(r.canonical_fixture_id)  # id only: the score is dropped on purpose
    return Universe(
        tuple(dev),
        tuple(sorted(ids[HOLDOUT])),
        tuple(sorted(ids[RESERVED])),
        tuple(sorted(ids[GUARD])),
        roles,
        known,
    )


def ids_hash(ids: tuple[str, ...] | list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Fold:
    eval_season: str
    train_seasons: tuple[str, ...]
    inner_validation_season: (
        str  # last train season: reserved for tuning in Sprint 3+ (never used to benchmark)
    )
    fit_cutoff_utc: datetime  # earliest kickoff of the eval season
    train_rows: tuple[EvalRow, ...]
    eval_rows: tuple[EvalRow, ...]


def make_folds(cfg: BenchConfig, universe: Universe, lag_hours: int) -> list[Fold]:
    """Expanding season-by-season walk-forward over DEV rows only.

    Train = DEV rows of earlier seasons whose inferred availability (kickoff + lag) is at or before the earliest
    kickoff of the eval season (an embargo that also covers seasons finishing late). Eval = every DEV row of the
    eval season, all competitions together: every model sees exactly the same fixtures."""
    by_season: dict[str, list[EvalRow]] = {}
    for r in universe.dev:
        by_season.setdefault(r.season, []).append(r)
    ordered = sorted(by_season, key=season_start_year)
    lag = timedelta(hours=lag_hours)
    folds = []
    for i, season in enumerate(ordered):
        earlier = ordered[:i]
        if len(earlier) < cfg.min_train_seasons:
            continue
        eval_rows = tuple(sorted(by_season[season], key=lambda r: (r.kickoff_utc, r.fixture_id)))
        cutoff = min(r.kickoff_utc for r in eval_rows)
        train = tuple(
            sorted(
                (r for s in earlier for r in by_season[s] if r.kickoff_utc + lag <= cutoff),
                key=lambda r: (r.kickoff_utc, r.fixture_id),
            )
        )
        if len(train) < cfg.min_train_matches:
            continue  # fail closed: too little history for an honest fit
        folds.append(Fold(season, tuple(earlier), earlier[-1], cutoff, train, eval_rows))
    return folds


def inner_split(fold: Fold) -> tuple[tuple[EvalRow, ...], tuple[EvalRow, ...]]:
    """(fit, validation) for candidates that tune hyper-parameters (Sprint 3+): the last train season is the
    validation block, strictly later than the fit block. The eval season is never touched."""
    fit = tuple(r for r in fold.train_rows if r.season != fold.inner_validation_season)
    val = tuple(r for r in fold.train_rows if r.season == fold.inner_validation_season)
    if not fit or not val:
        raise ValueError(f"fold {fold.eval_season} has no room for an inner validation split")
    if max(r.kickoff_utc for r in fit) >= min(r.kickoff_utc for r in val):
        raise ValueError("inner fit block is not strictly earlier than the validation block")
    return fit, val


def assert_disjoint(folds: list[Fold], universe: Universe) -> None:
    """No fixture is both train and eval in a fold; eval seasons never overlap; no frozen fixture appears."""
    frozen = set(universe.holdout_ids) | set(universe.reserved_ids) | set(universe.guard_ids)
    seen_eval: set[str] = set()
    for f in folds:
        tr = {r.fixture_id for r in f.train_rows}
        ev = {r.fixture_id for r in f.eval_rows}
        if tr & ev:
            raise AssertionError(f"fold {f.eval_season}: fixtures in both train and eval")
        if (tr | ev) & frozen:
            raise AssertionError(f"fold {f.eval_season}: a frozen (holdout/reserved/guard) fixture leaked in")
        if ev & seen_eval:
            raise AssertionError(f"fold {f.eval_season}: eval fixtures overlap an earlier fold")
        if any(r.kickoff_utc >= f.fit_cutoff_utc for r in f.train_rows):
            raise AssertionError(f"fold {f.eval_season}: a train fixture kicks off at/after the fit cutoff")
        seen_eval |= ev


def stage_cutoff(kickoff_utc: datetime, stage: SnapshotStage) -> datetime:
    return cutoff_for_stage(kickoff_utc, stage)


def history_leak_check(rows: list[EvalRow], lag_hours: int, stage: SnapshotStage) -> list[dict]:
    """Sequential-replay guard (Elo updates inside the eval season). For every row, the previous match of each of its
    two teams must be AVAILABLE (kickoff + lag) at the row's stage cutoff. Returns the violations (empty = clean).
    Date-only kickoffs are flagged separately by the caller."""
    lag = timedelta(hours=lag_hours)
    last: dict[str, tuple[datetime, str]] = {}
    bad = []
    for r in sorted(rows, key=lambda x: (x.kickoff_utc, x.fixture_id)):
        cutoff = stage_cutoff(r.kickoff_utc, stage)
        for team in (r.home_id, r.away_id):
            prev = last.get(team)
            if prev is not None and prev[0] + lag > cutoff:
                bad.append({"fixture_id": r.fixture_id, "team": team, "previous_fixture_id": prev[1]})
        last[r.home_id] = (r.kickoff_utc, r.fixture_id)
        last[r.away_id] = (r.kickoff_utc, r.fixture_id)
    return bad


def protocol_manifest(cfg: BenchConfig, universe: Universe, folds: list[Fold]) -> dict:
    return {
        "protocol_version": cfg.protocol_version,
        "config_fingerprint_sha256": cfg.fingerprint,
        "universe_counts": universe.counts(),
        "frozen": {
            "holdout": {
                "count": len(universe.holdout_ids),
                "fixture_ids_sha256": ids_hash(universe.holdout_ids),
            },
            "reserved": {
                "count": len(universe.reserved_ids),
                "fixture_ids_sha256": ids_hash(universe.reserved_ids),
            },
            "global_benchmark_guard": {
                "count": len(universe.guard_ids),
                "fixture_ids_sha256": ids_hash(universe.guard_ids),
            },
            "note": "frozen fixtures are listed by id hash only; their scores are not carried by the benchmark",
        },
        "roles": {f"{k[0]}|{k[1]}": v for k, v in sorted(universe.roles.items())},
        "folds": [
            {
                "eval_season": f.eval_season,
                "train_seasons": list(f.train_seasons),
                "inner_validation_season": f.inner_validation_season,
                "fit_cutoff_utc": f.fit_cutoff_utc.isoformat(),
                "train_matches": len(f.train_rows),
                "eval_matches": len(f.eval_rows),
                "train_fixture_ids_sha256": ids_hash([r.fixture_id for r in f.train_rows]),
                "eval_fixture_ids_sha256": ids_hash([r.fixture_id for r in f.eval_rows]),
            }
            for f in folds
        ],
    }


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
