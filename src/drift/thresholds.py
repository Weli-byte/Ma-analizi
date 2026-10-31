"""Alert thresholds chosen on validation HISTORY, not on the live data they will judge (ADR 0092).

Source: the out-of-fold predictions of the market model (`src.calibsel.oof`, ADR 0089), FIT and SELECT seasons only (2020-21 to 2022-23; the test season 2023-24 and the final-test seasons
are not read). For every pair of consecutive 60-day windows the drift statistic of the model's predicted probabilities (home win, over 2.5, BTTS) and of the league mix is computed; that is
how much the inputs move between two ordinary periods. The alert threshold of a statistic is the largest value seen in that history times a margin of 1.25: an alarm then means "more drift
than anything in three seasons of normal operation". Cooldown: an alarm key is announced at most once per day (the `notify_once` rule of ADR 0077).

`python -m src.drift.thresholds --oof <oof rows .jsonl> --out docs/phase3/s14_thresholds.json`
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.cli_utils import configure_output

from . import stats

HISTORY_SEASONS = ("2020-21", "2021-22", "2022-23")
WINDOW_DAYS = 60
MARGIN = 1.25
FEATURES = {"p_home": lambda r: r["p"][0], "p_over_2_5": lambda r: r["po"], "p_btts": lambda r: r["pb"]}


def calibrate(rows: list[dict]) -> dict:
    rows = sorted((r for r in rows if r["season"] in HISTORY_SEASONS), key=lambda r: r["kickoff"])
    if not rows:
        raise ValueError("no history rows")
    t0, t_end = datetime.fromisoformat(rows[0]["kickoff"]), datetime.fromisoformat(rows[-1]["kickoff"])
    windows = []
    start = t0
    while start < t_end:
        end = start + timedelta(days=WINDOW_DAYS)
        w = [r for r in rows if start <= datetime.fromisoformat(r["kickoff"]) < end]
        if len(w) >= stats.MIN_N:
            windows.append(w)
        start = end
    seen: dict[str, list[float]] = {}
    for a, b in zip(windows, windows[1:], strict=False):
        for name, f in FEATURES.items():
            m = stats.continuous([f(r) for r in a], [f(r) for r in b])
            for k in ("psi", "js", "ks"):
                seen.setdefault(f"{name}.{k}", []).append(m[k])
        m = stats.categorical([r["league"] for r in a], [r["league"] for r in b])
        seen.setdefault("league_mix.js", []).append(m["js"])
    return {"source": "market-model out-of-fold predictions (ADR 0089), fit + select seasons", "seasons": list(HISTORY_SEASONS), "window_days": WINDOW_DAYS, "window_pairs": len(windows) - 1,
            "margin": MARGIN, "thresholds": {k: round(max(v) * MARGIN, 6) for k, v in sorted(seen.items())}, "history_max": {k: round(max(v), 6) for k, v in sorted(seen.items())},
            "cooldown": "one announcement per alarm key per day (notify_once, ADR 0077)", "min_n": stats.MIN_N}  # fmt: skip


def main(argv=None) -> int:
    from src.calibsel.oof import read

    configure_output()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--oof", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = calibrate(read(Path(a.oof)))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(res["thresholds"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
