"""Evaluation rows: one per settled fixture, with the forecast the user could have seen and the market as it stood at that forecast's cutoff (ADR 0087).

Sources (all prospective, nothing is back-filled): the published match-intelligence artifacts (`artifacts/markets/<fixture>/intel-*.json`, last one with `information_cutoff` before kick-off, the
same rule as the live track record, ADR 0048), the settled result from `track_record.json`, and the stored exact quotes (`artifacts/odds`). The market side is rebuilt AS OF the cutoff.
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from src.markets.track import load_artifacts

from .consensus import SELECTIONS, consensus, read_quotes
from .devig import METHODS

OUTCOME = {"H": 0, "D": 1, "A": 2}


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def odds_index(odds_root) -> dict[str, list[Path]]:
    """{forecast key (home__away__kickoff date): [fixture directories]} from the fixtures' meta files."""
    out: dict[str, list[Path]] = {}
    base = Path(odds_root) / "artifacts" / "odds"
    for meta in sorted(base.glob("*/meta.json")) if base.exists() else []:
        m = json.loads(meta.read_text(encoding="utf-8"))
        key = f"{m['home_id']}__{m['away_id']}__{_utc(m['kickoff_utc']).date().isoformat()}"
        out.setdefault(key, []).append(meta.parent)
    return out


def build_rows(markets_root, odds_root, min_books: int) -> tuple[list[dict], dict]:
    markets_root = Path(markets_root)
    track = json.loads((markets_root / "artifacts" / "markets" / "track_record.json").read_text(encoding="utf-8"))
    arts = load_artifacts(markets_root)
    index = odds_index(odds_root)
    rep = {"settled": len(track["matches"]), "no_forecast_before_kickoff": 0, "no_odds_store": 0, "fewer_than_min_books": 0, "used": 0, "min_books": min_books}
    rows = []
    for m in track["matches"]:
        key = m["key"]
        kickoff = None
        if key in index:
            kickoff = _utc(json.loads((index[key][0] / "meta.json").read_text(encoding="utf-8"))["kickoff_utc"])
        before = [a for a in arts.get(key, []) if kickoff is None or _utc(a["information_cutoff"]) < kickoff]
        if not before:
            rep["no_forecast_before_kickoff"] += 1
            continue
        art = max(before, key=lambda a: a["information_cutoff"])
        if key not in index:
            rep["no_odds_store"] += 1
            continue
        cutoff = _utc(art["information_cutoff"])
        quotes = [q for d in index[key] for q in read_quotes(d)]
        cons = {meth: consensus(quotes, cutoff, meth, min_books) for meth in METHODS}
        if cons["normalization"] is None:
            rep["fewer_than_min_books"] += 1
            continue
        res = art["intelligence"]["result"]
        stored = res.get("market_probs_devig")
        rows.append({
            "key": key, "league": m["league"], "kickoff": kickoff, "cutoff": cutoff, "y": OUTCOME[m["result"]],
            "p_model": np.array([res["model_probs"][k] for k in ("home", "draw", "away")]),
            "p_headline": np.array([res["headline_probs"][k] for k in ("home", "draw", "away")]),
            "p_stored_market": None if not stored else np.array([stored[k] for k in ("home", "draw", "away")]),
            "market": {meth: c["p"] for meth, c in cons.items()}, "sharp": cons["normalization"]["sharp"],
            "n_books": cons["normalization"]["n_books"], "dispersion": cons["normalization"]["dispersion"], "staleness_min": cons["normalization"]["staleness_min"],
        })  # fmt: skip
    rows.sort(key=lambda r: (r["kickoff"], r["key"]))
    rep["used"] = len(rows)
    rep["selections"] = list(SELECTIONS)
    return rows, rep
