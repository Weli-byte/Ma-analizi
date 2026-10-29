"""Out-of-fold predictions of the market model on the open history (ADR 0089).

For every match of the requested seasons the market model is refitted every `refit_block_days` on matches that were finished before the block (the same walk-forward as `src.markets.evaluate`) and
predicts the block for the six domestic forecast leagues: 1X2 (model only, no odds), over 2.5 and BTTS from one score grid. Only seasons up to the last VALIDATION season of `configs/evaluation.yaml` may be requested: the final-test
seasons are refused here and are not even downloaded. The rows are deterministic for a given history; `digest()` fingerprints them.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory
from src.ingestion.openfootball import FILES
from src.markets.model import fit_markets, scoreline_matrix
from src.markets.of_source import load_world
from src.markets.predict import _data_quality

ROOT = Path(__file__).resolve().parents[2]


def build_oof(root: Path, seasons: tuple[str, ...]) -> tuple[list[dict], dict]:
    cdir = config_dir_for(root)
    cfg, ev = load_config("markets", cdir), load_config("evaluation", cdir)
    allowed = [*ev.train_seasons, *ev.validation_seasons]
    if set(seasons) & set(ev.final_test_seasons) or not set(seasons) <= set(allowed):
        raise ValueError("only train and validation seasons may be used; the final-test seasons are locked")
    last_year = max(int(s[:4]) for s in seasons)
    world = load_world(root, TeamDirectory.load(cdir / "team_aliases.yaml"), cfg, end_year=last_year)
    ms = [m for m in world.history if m.season in allowed]
    target = sorted((m for m in ms if m.season in seasons and m.league in FILES), key=lambda m: m.kickoff_utc)  # the six forecast leagues; cups and history-only leagues only feed the fit
    rows, fits = [], 0
    start, t_end = target[0].kickoff_utc, target[-1].kickoff_utc
    while start <= t_end:
        end = start + timedelta(days=cfg.refit_block_days)
        block = [m for m in target if start <= m.kickoff_utc < end]
        if block:
            mm = fit_markets(ms, start, cfg)
            fits += 1
            for m in block:
                lh, la = mm.goals.lambdas(m.home_id, m.away_id, m.league)
                mat = scoreline_matrix(lh, la, mm.rho, cfg.max_goals)
                i, j = np.indices(mat.shape)
                rows.append({
                    "league": m.league, "season": m.season, "kickoff": m.kickoff_utc.isoformat(),
                    "y": 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2, "o": int(m.home_goals + m.away_goals > 2), "b": int(m.home_goals > 0 and m.away_goals > 0),
                    "p": [float(mat[i > j].sum()), float(mat[i == j].sum()), float(mat[i < j].sum())], "po": float(mat[(i + j) > 2].sum()), "pb": float(mat[1:, 1:].sum()),
                    "low_confidence": bool(_data_quality(mm, m.home_id, m.away_id).get("low_confidence")),
                })  # fmt: skip
        start = end
    return rows, {"fits": fits, "matches": len(rows), "history_matches": len(ms), "data_version": world.data_version, "refit_block_days": cfg.refit_block_days}


def digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


def write(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8", newline="\n")


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def kickoff_of(row: dict) -> datetime:
    return datetime.fromisoformat(row["kickoff"])
