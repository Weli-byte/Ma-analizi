"""Sprint 4c (ADR 0071): opt-in extension of the Sprint 4 feature contract. It adds the three feature families that the plan listed and Sprint 4 did not build:

* calendar windows (last 90 and last 365 days) of goals for / against, with the number of matches in the window,
* an exponentially time-weighted form (half-life in days) of goals for / against,
* opponent-adjusted form: for each of a team's last N available matches, the goals it scored minus what the opponent usually concedes (and the goals it conceded minus what
  the opponent usually scores), the opponent's rates being shrunk toward the running league-wide mean and taken from the matches available BEFORE that match was applied.

The availability rule is the Sprint 4 one: a match enters the state only when kick-off + the inferred result lag <= the stage cutoff of the row being built, so a result published
after the cutoff can never reach a feature and fixtures sharing a kick-off never see each other. A value that cannot be computed is NaN with an `_available` flag, never 0. The frozen
`lm-s4-features-v1` contract is NOT changed; the extension has its own schema version and is evaluated by `s4c_benchmark.py` on development folds only."""

import bisect
import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from src.evaluation.dataset import EvalRow
from src.snapshot.stages import SnapshotStage, cutoff_for_stage

SCHEMA_VERSION = "lm-s4c-extra-features-v1"


@dataclass(frozen=True)
class ExtraConfig:
    windows_days: tuple[int, ...] = (90, 365)
    half_life_days: float = 60.0
    exp_max_matches: int = 40
    adjust_matches: int = 10
    shrink_k: float = 10.0
    min_matches: int = 3
    lag_hours: int = 3
    stage: SnapshotStage = SnapshotStage.T_24H

    @staticmethod
    def from_raw(raw: dict, lag_hours: int, stage: SnapshotStage) -> "ExtraConfig":
        e = raw["extra_features"]
        return ExtraConfig(tuple(e["windows_days"]), float(e["half_life_days"]), int(e["exp_max_matches"]), int(e["adjust_matches"]), float(e["shrink_k"]), int(e["min_matches"]),
                           lag_hours, stage)  # fmt: skip


def column_names(cfg: ExtraConfig) -> tuple[str, ...]:
    cols: list[str] = []
    for side in ("h", "a"):
        for w in cfg.windows_days:
            cols += [f"x_{side}_gf{w}d", f"x_{side}_ga{w}d", f"x_{side}_n{w}d"]
        cols += [f"x_{side}_gf_exp", f"x_{side}_ga_exp", f"x_{side}_adj_att", f"x_{side}_adj_def"]
    cols += ["x_adj_strength_diff", "x_extra_available"]
    return tuple(cols)


def schema_sha(cfg: ExtraConfig) -> str:
    return hashlib.sha256(json.dumps([SCHEMA_VERSION, list(column_names(cfg)), cfg.windows_days, cfg.half_life_days, cfg.adjust_matches, cfg.shrink_k], sort_keys=False).encode("utf-8")).hexdigest()


class _State:
    __slots__ = ("kicks", "gf", "ga", "adj_att", "adj_def", "gf_sum", "ga_sum", "n")

    def __init__(self, adjust_matches: int) -> None:
        self.kicks: list = []          # kick-offs of the team's available matches, ascending
        self.gf: list[int] = []
        self.ga: list[int] = []
        self.adj_att: deque = deque(maxlen=adjust_matches)
        self.adj_def: deque = deque(maxlen=adjust_matches)
        self.gf_sum = 0
        self.ga_sum = 0
        self.n = 0


def build_extra(rows: list[EvalRow], cfg: ExtraConfig) -> np.ndarray:
    """(n, p) float matrix aligned with `rows`; each row uses only matches available at its stage cutoff."""
    names = column_names(cfg)
    col = {nm: i for i, nm in enumerate(names)}
    n = len(rows)
    X = np.full((n, len(names)), np.nan)
    order = sorted(range(n), key=lambda i: (rows[i].kickoff_utc, rows[i].fixture_id))
    lag = timedelta(hours=cfg.lag_hours)
    by_avail = sorted(order, key=lambda i: (rows[i].kickoff_utc + lag, rows[i].fixture_id))
    teams: dict[str, _State] = {}
    totals = {"goals": 0, "sides": 0}

    def st(t: str) -> _State:
        s = teams.get(t)
        if s is None:
            s = teams[t] = _State(cfg.adjust_matches)
        return s

    def mu() -> float:
        return totals["goals"] / totals["sides"] if totals["sides"] else 1.3  # only used for shrinkage before any match is known; the prior mean is a constant, not a fitted value

    def rate(sum_goals: int, count: int) -> float:
        return (sum_goals + cfg.shrink_k * mu()) / (count + cfg.shrink_k)

    def apply_match(j: int) -> None:
        r = rows[j]
        gh, ga = int(r.home_goals or 0), int(r.away_goals or 0)
        h, a = st(r.home_id), st(r.away_id)
        # opponent rates BEFORE this match enters either state
        a_def_rate, a_att_rate = rate(a.ga_sum, a.n), rate(a.gf_sum, a.n)
        h_def_rate, h_att_rate = rate(h.ga_sum, h.n), rate(h.gf_sum, h.n)
        h.adj_att.append(gh - a_def_rate)
        h.adj_def.append(ga - a_att_rate)
        a.adj_att.append(ga - h_def_rate)
        a.adj_def.append(gh - h_att_rate)
        for s, gf, gc in ((h, gh, ga), (a, ga, gh)):
            s.kicks.append(r.kickoff_utc)
            s.gf.append(gf)
            s.ga.append(gc)
            s.gf_sum += gf
            s.ga_sum += gc
            s.n += 1
        totals["goals"] += gh + ga
        totals["sides"] += 2

    ptr = 0
    ln2 = math.log(2.0)
    for i in order:
        r = rows[i]
        cutoff = cutoff_for_stage(r.kickoff_utc, cfg.stage)
        while ptr < n and rows[by_avail[ptr]].kickoff_utc + lag <= cutoff:
            apply_match(by_avail[ptr])
            ptr += 1
        x = X[i]
        ok = 0
        for side, team in (("h", r.home_id), ("a", r.away_id)):
            s = st(team)
            for w in cfg.windows_days:
                lo = bisect.bisect_left(s.kicks, r.kickoff_utc - timedelta(days=w))
                cnt = len(s.kicks) - lo
                x[col[f"x_{side}_n{w}d"]] = float(cnt)
                if cnt >= cfg.min_matches:
                    x[col[f"x_{side}_gf{w}d"]] = float(np.mean(s.gf[lo:]))
                    x[col[f"x_{side}_ga{w}d"]] = float(np.mean(s.ga[lo:]))
            if s.n >= cfg.min_matches:
                k = s.kicks[-cfg.exp_max_matches:]
                age = np.array([(r.kickoff_utc - t).total_seconds() / 86400.0 for t in k])
                wt = np.exp(-ln2 * np.clip(age, 0.0, None) / cfg.half_life_days)
                x[col[f"x_{side}_gf_exp"]] = float(np.average(s.gf[-cfg.exp_max_matches:], weights=wt))
                x[col[f"x_{side}_ga_exp"]] = float(np.average(s.ga[-cfg.exp_max_matches:], weights=wt))
            if len(s.adj_att) >= cfg.min_matches:
                x[col[f"x_{side}_adj_att"]] = float(np.mean(s.adj_att))
                x[col[f"x_{side}_adj_def"]] = float(np.mean(s.adj_def))
                ok += 1
        if ok == 2:
            x[col["x_adj_strength_diff"]] = (x[col["x_h_adj_att"]] - x[col["x_h_adj_def"]]) - (x[col["x_a_adj_att"]] - x[col["x_a_adj_def"]])
        x[col["x_extra_available"]] = 1.0 if ok == 2 else 0.0
    return X


def perturbation_audit(rows: list[EvalRow], cfg: ExtraConfig, points) -> dict:
    """Build twice, the second time with every result at/after `point` replaced by 7-0: rows kicking off before `point` must be bit-identical (NaN equals NaN)."""
    import dataclasses

    base = build_extra(rows, cfg)
    out = {}
    for point in points:
        alt_rows = [dataclasses.replace(r, home_goals=7, away_goals=0, outcome=0) if r.kickoff_utc >= point else r for r in rows]
        alt = build_extra(alt_rows, cfg)
        before = np.array([r.kickoff_utc < point for r in rows])
        same = np.array_equal(base[before], alt[before], equal_nan=True)
        out[point.isoformat()] = {"rows_before_point": int(before.sum()), "rows_identical": bool(same),
                                  "violations": 0 if same else int((~np.isclose(base[before], alt[before], equal_nan=True)).any(axis=1).sum())}  # fmt: skip
    return {"definition": "all results at/after the point are replaced by 7-0; rows before the point must not change", "points": out, "violations": sum(v["violations"] for v in out.values())}
