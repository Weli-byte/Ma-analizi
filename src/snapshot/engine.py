"""S13 snapshot engine (ADR 0027): immutable, content-hashed per-stage snapshots + predictions.

Per fixture and stage (t-24h, t-90m, t-30m, kickoff):
  1. build an immutable `StageSnapshot` from leakage-safe features at the stage's information
     cutoff (`compute_features` only sees results available at that cutoff);
  2. lineup / injury availability is UNKNOWN (reason `no_provider`) -- nothing is invented, and
     models that do not need it simply run;
  3. fit-once models predict -> immutable `PredictionRecord`s; an abstaining/failed model is
     REPORTED, never filled;
  4. (optional, real API) LLM predictions are added by the caller via `run.py`;
  5. probability delta vs the previous locked stage, then the stage is locked (`store.py`).

The snapshot hash is content-derived and excludes wall-clock fields, so the same inputs always give
the same hash and the same system-side request payload. A re-run of a locked stage returns what is
stored; it never recomputes or overwrites it.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from src.config import FeaturesConfig
from src.evaluation.dataset import EvalRow
from src.features.compute import DEFAULT_CONFIG, compute_features
from src.features.history import MatchHistory
from src.schemas import PredictionRecord
from src.versioning import canonical_json

from .stages import SnapshotStage, cutoff_for_stage

UNKNOWN_OUTCOME = -1  # sentinel: the result does not exist yet (models never read it at predict time)
UNKNOWN_AVAILABILITY = {"status": "UNKNOWN", "reason": "no_provider"}
AVAILABILITY_FIELDS = ("lineups", "injuries")


@dataclass(frozen=True)
class StageSnapshot:
    fixture_id: str
    stage: str
    league_id: str
    season: str
    home_id: str
    away_id: str
    kickoff_utc: datetime
    information_cutoff: datetime
    data_version: str
    feature_version: str
    features: dict[str, float | None]
    unavailable_reasons: dict[str, str]
    availability: dict[str, dict]
    stage_cutoff: datetime | None = (
        None  # nominal cutoff of the stage (label); information_cutoff is the truth
    )
    snapshot_hash: str = field(default="")

    def content(self) -> dict:
        """Everything that defines the snapshot; deliberately NO wall-clock time."""
        return {
            "fixture_id": self.fixture_id,
            "stage": self.stage,
            "league_id": self.league_id,
            "season": self.season,
            "home_id": self.home_id,
            "away_id": self.away_id,
            "kickoff_utc": self.kickoff_utc.isoformat(),
            "information_cutoff": self.information_cutoff.isoformat(),
            "data_version": self.data_version,
            "feature_version": self.feature_version,
            "features": self.features,
            "unavailable_reasons": self.unavailable_reasons,
            "availability": self.availability,
            "stage_cutoff": self.stage_cutoff.isoformat() if self.stage_cutoff else None,
        }

    def to_dict(self) -> dict:
        return {**self.content(), "snapshot_hash": self.snapshot_hash}

    def to_eval_row(self) -> EvalRow:
        """Read surface for models/LLM prompts. `outcome` is the UNKNOWN sentinel: there is no result."""
        return EvalRow(
            self.fixture_id, self.league_id, self.season, self.kickoff_utc, self.home_id, self.away_id,
            UNKNOWN_OUTCOME, dict(self.features), dict(self.unavailable_reasons), {},
            availability=self.availability,
        )  # fmt: skip


def hash_content(content: dict) -> str:
    return hashlib.sha256(canonical_json(content).encode()).hexdigest()


def build_stage_snapshot(
    fixture,  # needs fixture_id, league_id, season, kickoff_utc, home_id, away_id
    history: MatchHistory,
    stage: SnapshotStage,
    data_version: str,
    feature_version: str,
    features_cfg: FeaturesConfig = DEFAULT_CONFIG,
    injuries: dict | None = None,
    lineups: dict | None = None,
    information_cutoff: datetime | None = None,
) -> StageSnapshot:
    # ADR 0027 amendment: a live run passes its actual snapshot time as the information cutoff, so
    # data fetched at run time (injuries, lineups) can never post-date it; the nominal stage cutoff
    # stays in the content as `stage_cutoff`.
    cutoff = information_cutoff or cutoff_for_stage(fixture.kickoff_utc, stage)
    availability = {k: dict(UNKNOWN_AVAILABILITY) for k in AVAILABILITY_FIELDS}
    if injuries:  # a real provider answer (OBSERVED) or an explicit FAILED -- never silently dropped
        availability["injuries"] = injuries
    if lineups:  # OBSERVED, UNKNOWN (not announced) or FAILED
        availability["lineups"] = lineups
    fr = compute_features(fixture, history, cutoff, features_cfg)  # raises if cutoff > kickoff
    snap = StageSnapshot(
        fixture.fixture_id, stage.value, fixture.league_id, fixture.season, fixture.home_id,
        fixture.away_id, fixture.kickoff_utc, cutoff, data_version, feature_version,
        dict(fr.values), dict(fr.reasons), availability, cutoff_for_stage(fixture.kickoff_utc, stage),
    )  # fmt: skip
    return StageSnapshot(**{**snap.__dict__, "snapshot_hash": hash_content(snap.content())})


@dataclass(frozen=True)
class ModelStatus:
    model_id: str
    status: str  # "predicted" | "abstained" | "error"
    detail: str = ""


def predict_stage(
    snapshot: StageSnapshot, models: list, generated_at: datetime
) -> tuple[list[PredictionRecord], list[ModelStatus]]:
    """One immutable PredictionRecord per model that can predict. Nothing is substituted for a
    model that abstains (non-finite probabilities) or raises -- it is reported in the statuses."""
    row = snapshot.to_eval_row()
    records, statuses = [], []
    for m in models:
        try:
            # models that replay outcomes into their state (Elo) expose a read-only path
            predict = getattr(m, "predict_proba_pending", m.predict_proba)
            p = np.asarray(predict([row]), dtype=float)
        except Exception as e:  # noqa: BLE001 - reported per model, never hidden
            statuses.append(ModelStatus(m.model_id, "error", f"{type(e).__name__}: {e}"[:200]))
            continue
        if p.shape != (1, 3) or not np.isfinite(p).all():
            statuses.append(ModelStatus(m.model_id, "abstained", "model could not predict this fixture"))
            continue
        records.append(
            PredictionRecord(
                fixture_id=snapshot.fixture_id,
                model_id=m.model_id,
                model_version=m.model_version,
                feature_version=snapshot.feature_version,
                data_version=snapshot.data_version,
                kickoff_utc=snapshot.kickoff_utc,
                information_cutoff=snapshot.information_cutoff,
                generated_at=generated_at,
                p_home=float(p[0, 0]),
                p_draw=float(p[0, 1]),
                p_away=float(p[0, 2]),
            )
        )
        statuses.append(ModelStatus(m.model_id, "predicted"))
    return records, statuses
