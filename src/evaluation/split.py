"""Chronological split contract (ADR 0012). Config-driven and actually consumed:
`split_strategy`, `min_train_seasons`, `rolling_window_seasons` shape the walk-forward folds.
"""

import hashlib
from dataclasses import dataclass

from src.config import EvaluationConfig
from src.data.dataset import DatasetRef, open_db
from src.versioning import canonical_json


class SplitError(ValueError):
    pass


@dataclass(frozen=True)
class Fold:
    index: int
    train_seasons: tuple[str, ...]
    test_season: str


def walk_forward_folds(cfg: EvaluationConfig) -> list[Fold]:
    """Folds over train+validation seasons only (final-test seasons never participate)."""
    seasons = [*cfg.train_seasons, *cfg.validation_seasons]
    if len(seasons) <= cfg.min_train_seasons:
        raise SplitError("not enough seasons for min_train_seasons")
    folds = []
    for i in range(cfg.min_train_seasons, len(seasons)):
        if cfg.split_strategy == "expanding":
            train = seasons[:i]
        else:  # rolling
            train = seasons[max(0, i - cfg.rolling_window_seasons) : i]
        if not (train and max(train) < seasons[i]):
            raise SplitError(f"fold {i} is not chronological")
        folds.append(Fold(len(folds), tuple(train), seasons[i]))
    return folds


def build_split_manifest(cfg: EvaluationConfig, ref: DatasetRef) -> dict:
    """Split manifest: periods, cutoff timestamps, row counts, season counts. Reads only
    aggregate metadata (no labels/features), so it does not touch final-test data content."""
    con = open_db(ref.db_path)
    stats = {
        season: (n, lo, hi)
        for season, n, lo, hi in con.execute(
            "SELECT season, count(*), min(kickoff_utc), max(kickoff_utc) FROM fixtures "
            "WHERE status='finished' GROUP BY season"
        ).fetchall()
    }
    con.close()
    in_split = {*cfg.train_seasons, *cfg.validation_seasons, *cfg.final_test_seasons}
    missing = sorted(in_split - set(stats))
    if missing:
        raise SplitError(f"seasons in the split but absent from dataset {ref.data_version}: {missing}")

    def block(seasons: list[str]) -> dict:
        n = sum(stats[s][0] for s in seasons)
        return {
            "seasons": list(seasons),
            "n_seasons": len(seasons),
            "rows": n,
            "start_utc": min(stats[s][1] for s in seasons).isoformat(),
            "end_utc": max(stats[s][2] for s in seasons).isoformat(),
        }

    train, val, final = (
        block(cfg.train_seasons),
        block(cfg.validation_seasons),
        block(cfg.final_test_seasons),
    )
    if not (train["end_utc"] < val["start_utc"] < val["end_utc"] < final["start_utc"]):
        raise SplitError("split periods overlap or are not chronological")
    core = {
        "dataset_id": ref.data_version,
        "dataset_content_hash": ref.meta["content_hash"],
        "strategy": cfg.split_strategy,
        "min_train_seasons": cfg.min_train_seasons,
        "rolling_window_seasons": cfg.rolling_window_seasons,
        "train_period": train,
        "validation_period": val,
        "final_test_period": final,
        "cutoffs": {
            "train_end_utc": train["end_utc"],
            "validation_start_utc": val["start_utc"],
            "validation_end_utc": val["end_utc"],
            "final_test_start_utc": final["start_utc"],
        },
        "folds": [
            {"index": f.index, "train_seasons": list(f.train_seasons), "test_season": f.test_season}
            for f in walk_forward_folds(cfg)
        ],
        "excluded_seasons": sorted(set(stats) - in_split),  # e.g. current partial season
    }
    split_id = "split-" + hashlib.sha256(canonical_json(core).encode()).hexdigest()[:12]
    return {"split_id": split_id, **core}
