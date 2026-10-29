"""Odds-aware ensemble research (ADR 0087). The fixture `real_oddsens_excerpt.json` is a REAL, trimmed excerpt of the stored data: five settled 2026-10-09/10 fixtures with their published forecasts, the settled result and the
quotes of the last two provider timestamps before the forecast cutoff plus one later one (The Odds API via the cloud collector). Rows labelled TEST_ONLY are synthetic and only exercise the split."""

import json
import shutil
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from src.oddsens import consensus, dataset, devig, evaluate, report

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """Writes the excerpt back into the directory layout of the stores (markets-data / odds-data checkouts)."""
    data = json.loads(EXCERPT.read_text(encoding="utf-8"))
    root = tmp_path_factory.mktemp("oddsens")
    mk, od = root / "markets", root / "odds"
    rows = []
    for key, m in data["matches"].items():
        rows.append({"key": key, **m["result_row"]})
        for f in m["intel"]:
            (mk / "artifacts" / "markets" / key).mkdir(parents=True, exist_ok=True)
            (mk / "artifacts" / "markets" / key / f["file"]).write_text(json.dumps(f["body"]), encoding="utf-8")
        for o in m["odds"]:
            d = od / "artifacts" / "odds" / o["fixture"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "meta.json").write_text(json.dumps(o["meta"]), encoding="utf-8")
            (d / "quotes.jsonl").write_text("\n".join(json.dumps(q) for q in o["quotes"]) + "\n", encoding="utf-8")
    (mk / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"generated_at": data["track_generated_at"], "matches": rows}), encoding="utf-8")
    return mk, od


@pytest.fixture(scope="module")
def rows_rep(tree):
    return dataset.build_rows(tree[0], tree[1], 3)


def test_every_method_returns_a_distribution_that_keeps_the_ordering_and_removes_the_margin():
    for odds in ((1.5, 4.2, 7.0), (2.1, 3.0, 3.6), (1.05, 15.0, 40.0), (2.9, 2.9, 2.9)):
        for meth in devig.METHODS:
            p = devig.remove_vig(odds, meth)
            assert p.shape == (3,) and abs(p.sum() - 1) < 1e-9 and np.all(p > 0) and np.all(p < 1)
            assert list(np.argsort(p)) == list(np.argsort(-np.asarray(odds)))                # shorter price, higher probability
            assert np.all(p <= 1 / np.asarray(odds) + 1e-9)                                  # the margin is taken out, never added
    assert devig.overround((2.0, 3.5, 4.0)) == pytest.approx(0.5 + 1 / 3.5 + 0.25 - 1)
    even = devig.remove_vig((3.0, 3.0, 3.0), "shin")
    assert even == pytest.approx([1 / 3] * 3)
    fair = devig.remove_vig((2.0, 4.0, 4.0), "power")                                         # no margin: all methods return the implied probabilities
    assert fair == pytest.approx([0.5, 0.25, 0.25]) and devig.remove_vig((2.0, 4.0, 4.0), "shin") == pytest.approx([0.5, 0.25, 0.25])
    two = devig.remove_vig((1.9, 2.0), "shin")
    assert two.shape == (2,) and abs(two.sum() - 1) < 1e-9
    with pytest.raises(ValueError):
        devig.remove_vig((1.0, 3.0, 3.0), "power")
    with pytest.raises(ValueError):
        devig.remove_vig((2.0, 3.0, 3.0), "nope")


def test_power_and_shin_take_more_margin_from_the_long_shot_than_normalization():
    odds = (1.4, 4.6, 9.0)
    n, pw, sh = (devig.remove_vig(odds, m) for m in ("normalization", "power", "shin"))
    assert pw[2] < n[2] and sh[2] < n[2] and pw[0] > n[0] and sh[0] > n[0]


def test_the_real_rows_are_rebuilt_as_of_the_forecast_cutoff(rows_rep):
    rows, rep = rows_rep
    assert rep["settled"] == 5 and rep["used"] == 5 and rep["no_odds_store"] == 0
    for r in rows:
        assert r["n_books"] >= 3 and abs(r["market"]["normalization"].sum() - 1) < 1e-9 and r["cutoff"] < r["kickoff"] and r["y"] in (0, 1, 2)
        assert np.abs(r["market"]["normalization"] - r["p_stored_market"]).max() < 0.03          # the rebuilt consensus agrees with the market stored in the published forecast
    assert [r["kickoff"] for r in rows] == sorted(r["kickoff"] for r in rows)


def test_quotes_stamped_or_fetched_after_the_cutoff_are_invisible(tree):
    fx = next((tree[1] / "artifacts" / "odds").iterdir())
    quotes = consensus.read_quotes(fx)
    stamps = sorted({q.provider_ts for q in quotes})
    assert len(stamps) >= 2
    early = consensus.triples_at(quotes, stamps[0])
    late = consensus.triples_at(quotes, stamps[-1] + timedelta(minutes=1))
    assert all(v["provider_ts"] <= stamps[0] for v in early.values()) and len(late) >= len(early)
    assert consensus.triples_at(quotes, stamps[0] - timedelta(hours=1)) == {}                    # nothing was visible before the first quote
    delayed = [q.__class__(q.bookmaker, q.selection, q.odds, q.provider_ts, stamps[-1] + timedelta(days=1)) for q in quotes]
    assert consensus.triples_at(delayed, stamps[-1] + timedelta(minutes=1)) == {}                # fetched later than the cutoff: unknown at that time
    assert consensus.consensus(quotes, stamps[-1] + timedelta(minutes=1), "power", min_books=3)["n_books"] >= 3
    assert consensus.consensus(quotes, stamps[-1] + timedelta(minutes=1), "power", min_books=99) is None
    stale = consensus.triples_at(quotes, stamps[-1] + consensus.MAX_STALENESS + timedelta(hours=1))
    assert stale == {}                                                                           # older than the staleness limit: not used


def test_approximate_other_market_and_invalid_quotes_are_ignored(tmp_path):
    d = tmp_path / "f"
    d.mkdir()
    base = {"bookmaker": "B (b)", "market": "1X2", "observed_at": "2026-10-09T10:00:05Z", "provider_timestamp": "2026-10-09T10:00:00Z", "provenance": {"bookmaker_key": "b"}, "timestamp_quality": "exact"}
    good = [{**base, "selection": s, "decimal_odds": o} for s, o in (("H", 2.0), ("D", 3.4), ("A", 3.9))]
    bad = [{**base, "selection": "H", "decimal_odds": 2.2, "timestamp_quality": "approximate"}, {**base, "selection": "D", "market": "OU", "decimal_odds": 1.9}, {**base, "selection": "A", "decimal_odds": 1.0}]
    (d / "quotes.jsonl").write_text("\n".join(json.dumps(x) for x in good + bad) + "\n", encoding="utf-8")
    q = consensus.read_quotes(d)
    assert len(q) == 3 and {x.odds for x in q} == {2.0, 3.4, 3.9}


def _synthetic(n, model_good_first_half):
    """TEST_ONLY rows: the model is right in the first half and wrong in the second (or the reverse); the market is the same everywhere."""
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n):
        y = int(rng.integers(0, 3))
        sharp = np.full(3, 0.2)
        sharp[y] = 0.6
        flat = np.array([1 / 3] * 3)
        wrong = np.full(3, 0.2)
        wrong[(y + 1) % 3] = 0.6
        good = (i < n // 2) == model_good_first_half
        market = 0.5 * sharp + 0.5 * flat
        kickoff = np.datetime64("2026-10-01") + i
        rows.append({"key": f"k{i}", "league": "EPL", "kickoff": kickoff, "cutoff": kickoff, "y": y, "p_model": sharp if good else wrong, "p_headline": market,
                     "p_stored_market": market, "market": {m: market for m in devig.METHODS}, "sharp": market, "n_books": 5, "dispersion": 0.0, "staleness_min": 0.0})  # fmt: skip
    return rows


def test_selection_and_report_halves_are_disjoint_and_choices_come_from_the_first_half_only():
    rows = _synthetic(60, model_good_first_half=True)                                            # the model looks perfect where the choice is made and terrible where it is reported
    ev = evaluate.evaluate(rows)
    assert ev["n_selection"] == 30 and ev["n_report"] == 30 and ev["selected_weight"] < 1.0       # a model-heavy blend wins on the selection half ...
    w = ev["selected_weight"]
    rep = ev["report"][f"convex blend w={w} (selected)"]
    mk = ev["report"][f"market only ({ev['selected_method']}, selected)"]
    assert rep["log_loss"] > mk["log_loss"]                                                      # ... and the report half exposes it: nothing was tuned on it
    assert ev["report_paired_vs_market"][f"convex blend w={w} (selected)"]["ci95"][0] > 0
    assert ev["stacking"]["status"] == "NOT_RUN" and ev["switch_gate"]["status"] == "NOT_MET"
    assert evaluate.evaluate(rows[:10])["status"] == "TOO_FEW_ROWS"


def test_the_metrics_and_the_paired_bootstrap_are_deterministic():
    rows = _synthetic(40, model_good_first_half=False)
    a, b = evaluate.evaluate(rows), evaluate.evaluate(rows)
    assert a["report_paired_vs_market"] == b["report_paired_vs_market"]
    y = np.array([0, 1, 2])
    p = np.eye(3)
    assert evaluate.log_loss(p, y).max() < 1e-9 and evaluate.brier(p, y).max() == 0 and evaluate.rps(p, y).max() == 0 and evaluate.accuracy(p, y).min() == 1


def test_the_report_on_the_real_excerpt_states_the_limits(rows_rep, tmp_path):
    rows, rep = rows_rep
    many = rows * 5                                                                              # only to reach the minimum row count of the report; the numbers are not interpreted
    for i, r in enumerate(many):
        r = dict(r)
        r["kickoff"] = r["kickoff"] + timedelta(days=i)
        many[i] = r
    ev = evaluate.evaluate(many)
    text = report.build(many, rep, ev, "test")
    assert "not used for any choice" in text and "production unchanged" in text and "NOT_RUN" in text and "no market quotes for them are collected" in text
    shutil.rmtree(tmp_path, ignore_errors=True)
