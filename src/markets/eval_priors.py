"""`python -m src.markets.eval_priors`: choose the UEFA-prior weights on VALIDATION seasons only (ADR 0056).

Rolling origin: at 1 Oct, 1 Jan and 1 Mar of the validation seasons the goals model is refitted on matches whose
result was available then, with each pair of prior weights on a grid, and the cup matches of the next 90 days are
scored (1X2 log loss, Dixon-Coles term off: it changes all grid points the same way). Weight (0, 0) is the
no-prior baseline. The final-test seasons are never downloaded (`end_year`). The result is a table, not a verdict:
a weight pair is adopted only if it beats the baseline on the validation seasons AND the bootstrap interval of the
paired loss difference excludes zero.
"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path

import numpy as np

from src.config import config_dir_for, load_config
from src.data.teams import TeamDirectory

from .coefficients import CUPS, uefa_priors
from .model import decay_weights, fit_rates, scoreline_matrix
from .of_source import load_world

ROOT = Path(__file__).resolve().parents[2]
COUNTRY_GRID = [0.0, 0.1, 0.2, 0.3, 0.5]
CLUB_GRID = [0.0, 0.2, 0.5, 0.8, 1.0]  # the config bound is 1.0
SEED = 20261013


def origins(validation_seasons: list[str]) -> list[datetime]:
    out = []
    for s in validation_seasons:
        y = int(s[:4])
        out += [
            datetime(y, 10, 1, tzinfo=UTC),
            datetime(y + 1, 1, 1, tzinfo=UTC),
            datetime(y + 1, 3, 1, tzinfo=UTC),
        ]
    return out


def losses(
    history, origin: datetime, cfg, country_w: float, club_w: float, horizon_days: int = 90
) -> list[float]:
    ms = [m for m in history if m.available_at <= origin]
    target = [
        m
        for m in history
        if m.league in CUPS and origin <= m.kickoff_utc < origin + timedelta(days=horizon_days)
    ]
    if not target or len(ms) < 200:
        return []
    w = decay_weights([m.kickoff_utc for m in ms], origin, cfg.half_life_days)
    table = fit_rates(
        np.array([m.home_goals for m in ms], float),
        np.array([m.away_goals for m in ms], float),
        [m.home_id for m in ms],
        [m.away_id for m in ms],
        [m.league for m in ms],
        w,
        cfg,
        np.array([m.neutral for m in ms], bool),
        uefa_priors(ms, origin, country_w, club_w),
    )
    out = []
    for m in target:
        lh, la = table.lambdas(m.home_id, m.away_id, m.league, m.neutral)
        mat = scoreline_matrix(lh, la, 0.0, cfg.max_goals)
        i, j = np.indices(mat.shape)
        p = np.array([mat[i > j].sum(), mat[i == j].sum(), mat[i < j].sum()])
        y = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
        out.append(float(-np.log(max(p[y], 1e-12))))
    return out


def evaluate(root: Path = ROOT) -> dict:
    cdir = config_dir_for(root)
    cfg = load_config("markets", cdir)
    ev = load_config("evaluation", cdir)
    last_year = int(ev.validation_seasons[-1][:4])
    world = load_world(root, TeamDirectory.load(cdir / "team_aliases.yaml"), cfg, end_year=last_year)
    cutoffs = origins(list(ev.validation_seasons))
    rows, base = [], None
    for cw, kw in product(COUNTRY_GRID, CLUB_GRID):
        ls = [x for o in cutoffs for x in losses(world.history, o, cfg, cw, kw)]
        if (cw, kw) == (0.0, 0.0):
            base = np.array(ls)
        rows.append(
            {"country": cw, "club": kw, "log_loss": float(np.mean(ls)), "n": len(ls), "_l": np.array(ls)}
        )
    rng = np.random.default_rng(SEED)
    for r in rows:
        d = r["_l"] - base
        boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(1000)]
        r["minus_baseline"] = float(d.mean())
        r["ci95"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
        del r["_l"]
    best = min(rows, key=lambda r: r["log_loss"])
    return {
        "validation_seasons": list(ev.validation_seasons),
        "origins": [o.isoformat() for o in cutoffs],
        "grid": rows,
        "best": best,
        "adopt": bool(best["ci95"][1] < 0),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    res = evaluate(Path(a.root))
    out = Path(a.root) / "artifacts" / "markets" / "_eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "uefa_priors.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    for r in sorted(res["grid"], key=lambda r: r["log_loss"])[:8]:
        print(f"country {r['country']:.1f} club {r['club']:.1f}  log loss {r['log_loss']:.4f}  vs baseline {r['minus_baseline']:+.4f}  ci95 {r['ci95'][0]:+.4f}..{r['ci95'][1]:+.4f}")  # fmt: skip
    print(
        "baseline (0, 0) log loss:",
        next(r["log_loss"] for r in res["grid"] if r["country"] == 0 == r["club"]),
    )
    print("best:", res["best"]["country"], res["best"]["club"], "adopt:", res["adopt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
