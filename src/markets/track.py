"""Live track record (ADR 0048): published match-intelligence artifacts scored against the real results.

For every finished match we take the LAST artifact whose `information_cutoff` is before kickoff (the forecast
a user could actually have seen) and score it: 1X2 (log loss, Brier, RPS) against the league base rate and,
where an exact market existed, against the market; over/under 2.5; BTTS; correct-score hits; and how often the
published tips came true per lean label. Results come from the public-domain openfootball files. Nothing here
changes a stored artifact. With few matches the numbers are noise and the report says so.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .data import StatMatch

ROOT = Path(__file__).resolve().parents[2]
EPS = 1e-12
MIN_RELIABLE = 100  # below this many scored matches the report calls itself noise


def _outcome(m: StatMatch) -> int:
    return 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2


def _rps(p: np.ndarray, y: int) -> float:
    return float(np.sum((np.cumsum(p) - np.cumsum(np.eye(3)[y]))[:-1] ** 2) / 2)


def _tip_true(tip: dict, m: StatMatch) -> bool | None:
    """Did a published tip come true? None when the tip's market cannot be judged from the score alone."""
    out, total = _outcome(m), m.home_goals + m.away_goals
    market, pick = tip["market"], tip["pick"]
    if market == "match_result":
        return {"Home win": 0, "Draw": 1, "Away win": 2}[pick] == out
    if market == "double_chance":
        return out in {"1X": (0, 1), "12": (0, 2), "X2": (1, 2)}[pick]
    if market == "goals_2_5":
        return (total > 2) == pick.startswith("Over")
    if market == "btts":
        both = m.home_goals > 0 and m.away_goals > 0
        return both == (pick == "Both teams score")
    return None  # corners / cards / shots need statistics the open source does not have


def wilson(hits: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson interval of a hit rate (None without matches)."""
    if n == 0:
        return None
    p = hits / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def hit_summary(flags: list[bool]) -> dict:
    n, hits = len(flags), sum(flags)
    return {"n": n, "hits": hits, "rate": (hits / n) if n else None, "ci95": wilson(hits, n)}


def load_artifacts(root: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for d in sorted((root / "artifacts" / "markets").glob("*__*__*")):
        files = sorted(d.glob("intel-*.json"))
        if files:
            out[d.name] = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    return out


def track_record(
    root: Path, results: list[StatMatch], now: datetime | None = None, names: dict[str, str] | None = None
) -> dict:
    now = now or datetime.now(UTC)
    by_key = {(m.home_id, m.away_id, m.kickoff_utc.date().isoformat()): m for m in results}
    arts_all = load_artifacts(root)
    first_cutoff = min(
        (datetime.fromisoformat(a["information_cutoff"]) for arts in arts_all.values() for a in arts),
        default=now,
    )
    prior = [
        m for m in results if m.available_at <= first_cutoff
    ] or results  # base rate known before any forecast
    base_counts = np.bincount([_outcome(m) for m in prior], minlength=3).astype(float)
    base = base_counts / base_counts.sum()
    names = names or {}
    listing = []
    rows, tips = [], {"strong lean": [0, 0], "lean": [0, 0], "toss-up": [0, 0]}
    for key, arts in arts_all.items():
        home, away, day = key.split("__")
        m = by_key.get((home, away, day))
        if m is None or m.kickoff_utc > now:
            continue
        before = [a for a in arts if datetime.fromisoformat(a["information_cutoff"]) < m.kickoff_utc]
        if not before:
            continue  # only artifacts generated after kickoff exist: not a forecast
        art = max(before, key=lambda a: a["information_cutoff"])
        intel, out = art["intelligence"], _outcome(m)
        res = intel["result"]
        h = np.array([res["headline_probs"][k] for k in ("home", "draw", "away")])
        mod = np.array([res["model_probs"][k] for k in ("home", "draw", "away")])
        mk = res["market_probs_devig"]
        mkt = np.array([mk[k] for k in ("home", "draw", "away")]) if mk else None
        over = next(x for x in intel["goals"]["over_under"] if x["line"] == 2.5)["over"]
        o = int(m.home_goals + m.away_goals > 2)
        b = int(m.home_goals > 0 and m.away_goals > 0)
        pick = int(np.argmax(h))
        side = ("H", "D", "A")
        listing.append(
            {
                "key": key, "date": m.kickoff_utc.date().isoformat(), "league": art["league"],
                "home": names.get(home, home), "away": names.get(away, away),
                "score": f"{m.home_goals}-{m.away_goals}", "result": side[out],
                "pick": side[pick], "pick_prob": float(h[pick]), "pick_hit": pick == out,
                "market_driven": mkt is not None,
                "score_pick": intel["most_likely_score"], "score_hit": intel["most_likely_score"] == f"{m.home_goals}-{m.away_goals}",
                "ou_pick": "Üst" if over >= 0.5 else "Alt", "ou_hit": (over >= 0.5) == bool(o),
                "btts_pick": "Var" if intel["goals"]["btts_yes"] >= 0.5 else "Yok",
                "btts_hit": (intel["goals"]["btts_yes"] >= 0.5) == bool(b),
                "over_actual": bool(o), "generated_at": art["generated_at"],
            }
        )  # fmt: skip
        rows.append(
            {
                "key": key,
                "league": art["league"],
                "ll": -np.log(max(h[out], EPS)),
                "ll_model": -np.log(max(mod[out], EPS)),
                "ll_base": -np.log(max(base[out], EPS)),
                "ll_market": None if mkt is None else -np.log(max(mkt[out], EPS)),
                "brier": float(np.sum((h - np.eye(3)[out]) ** 2)),
                "rps": _rps(h, out),
                "o25": -np.log(max(over if o else 1 - over, EPS)),
                "btts": -np.log(
                    max(intel["goals"]["btts_yes"] if b else 1 - intel["goals"]["btts_yes"], EPS)
                ),
                "score_hit": intel["most_likely_score"] == f"{m.home_goals}-{m.away_goals}",
                "pick_hit": int(np.argmax(h)) == out,
            }  # fmt: skip
        )
        for t in intel["tips"]:
            ok = _tip_true(t, m)
            if ok is not None:
                tips[t["lean"]][0] += int(ok)
                tips[t["lean"]][1] += 1
    n = len(rows)

    def mean(k, sub=None):
        v = [r[k] for r in (sub or rows) if r[k] is not None]
        return float(np.mean(v)) if v else None

    with_mkt = [r for r in rows if r["ll_market"] is not None]
    return {
        "generated_at": now.isoformat(),
        "matches_scored": n,
        "reliability_note": (
            "noise: too few matches to conclude anything"
            if n < MIN_RELIABLE
            else "enough matches for a first read; still a short record"
        ),  # fmt: skip
        "headline_1x2": {
            "log_loss": mean("ll"),
            "log_loss_model_only": mean("ll_model"),
            "log_loss_league_base_rate": mean("ll_base"),
            "brier": mean("brier"),
            "rps": mean("rps"),
            "top_pick_accuracy": mean("pick_hit"),
            "n_with_exact_market": len(with_mkt),
            "log_loss_market_on_those": mean("ll_market", with_mkt),
            "log_loss_headline_on_those": mean("ll", with_mkt),
        },  # fmt: skip
        "over_under_2_5_log_loss": mean("o25"),
        "btts_log_loss": mean("btts"),
        "correct_score_hit_rate": mean("score_hit"),
        "tips_by_lean": {
            k: {"hit": v[0], "n": v[1], "rate": (v[0] / v[1]) if v[1] else None} for k, v in tips.items()
        },
        "hit_rates": {
            "1x2": hit_summary([x["pick_hit"] for x in listing]),
            "over_under_2_5": hit_summary([x["ou_hit"] for x in listing]),
            "btts": hit_summary([x["btts_hit"] for x in listing]),
            "correct_score": hit_summary([x["score_hit"] for x in listing]),
            "1x2_market_driven": hit_summary([x["pick_hit"] for x in listing if x["market_driven"]]),
            "1x2_model_only": hit_summary([x["pick_hit"] for x in listing if not x["market_driven"]]),
        },
        "naive_hit_rates": {  # a fixed guess that needs no model, over the same matches
            "1x2_always_home": hit_summary([x["result"] == "H" for x in listing])["rate"],
            "over_2_5_always_over": hit_summary([x["over_actual"] for x in listing])["rate"],
        },
        "matches": sorted(listing, key=lambda x: (x["date"], x["key"]), reverse=True),
        "by_league": {
            lg: {"n": len(sub), "log_loss": mean("ll", sub)}
            for lg in sorted({r["league"] for r in rows})
            for sub in [[r for r in rows if r["league"] == lg]]
        },  # fmt: skip
    }


def main(argv=None) -> int:
    from src.config import config_dir_for, load_config
    from src.data.teams import TeamDirectory

    from .of_source import load_world

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    root = Path(a.root)
    cdir = config_dir_for(root)
    world = load_world(root, TeamDirectory.load(cdir / "team_aliases.yaml"), load_config("markets", cdir))
    directory = TeamDirectory.load(cdir / "team_aliases.yaml")
    names = {t: v["canonical_name"] for t, v in directory.teams.items()}
    rep = track_record(root, world.history, names=names)
    out = root / "artifacts" / "markets" / "track_record.json"
    out.write_text(json.dumps(rep, indent=2, default=float), encoding="utf-8")
    print(json.dumps(rep, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
