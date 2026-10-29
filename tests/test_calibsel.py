"""Calibration candidates and the chronological selection protocol (ADR 0089). `real_calibsel_oof_excerpt.jsonl` is a REAL excerpt (every k-th row, 250 per season) of the out-of-fold predictions
of the market model on the open history, seasons 2020-21 to 2023-24, built by `src.calibsel.oof` on 2026-10-29. Rows labelled TEST_ONLY are synthetic and only check that a calibrator recovers a
known distortion and that the protocol never lets the test window influence a choice."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.calibsel import calibrators as cal
from src.calibsel import labels, oof, protocol

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "tests" / "fixtures" / "real_calibsel_oof_excerpt.jsonl"


def _softmax(z):
    e = np.exp(z - z.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def _distorted(n=4000, power=2.0, seed=1):
    """TEST_ONLY: true probabilities, labels drawn from them, and a forecast that is over-confident by `power` on the log scale."""
    rng = np.random.default_rng(seed)
    z = rng.normal(0, 0.6, size=(n, 3))
    p_true = _softmax(z)
    y = np.array([rng.choice(3, p=p) for p in p_true])
    return _softmax(np.log(p_true) * power), y, p_true


def test_temperature_and_dirichlet_undo_a_known_over_confidence_and_keep_distributions():
    p, y, _ = _distorted()
    t = cal.fit("temperature", "1x2", p, y)
    assert t.params["T"] == pytest.approx(2.0, rel=0.15)
    for kind in cal.MULTICLASS:
        c = cal.fit(kind, "1x2", p, y)
        q = c.apply(p)
        assert q.shape == p.shape and np.allclose(q.sum(axis=1), 1) and np.all(q > 0)
        assert protocol.summary("1x2", q, y)["log_loss"] <= protocol.summary("1x2", p, y)["log_loss"] + 1e-9
    assert np.allclose(cal.fit("identity", "1x2", p, y).apply(p), p)


def test_binary_calibrators_recover_a_known_slope_and_isotonic_needs_rows():
    rng = np.random.default_rng(2)
    t = rng.normal(0, 1.2, 5000)
    y = (rng.random(5000) < 1 / (1 + np.exp(-t))).astype(int)
    p = 1 / (1 + np.exp(-2 * t))                                     # twice too steep
    pl = cal.fit("platt", "over_2_5", p, y)
    assert pl.params["a"] == pytest.approx(0.5, rel=0.15) and abs(pl.params["b"]) < 0.1
    iso = cal.fit("isotonic", "over_2_5", p, y)
    q = iso.apply(np.sort(p))
    assert np.all(np.diff(q) >= -1e-12) and q.min() >= 1e-3 and q.max() <= 1 - 1e-3
    with pytest.raises(ValueError, match="isotonic needs"):
        cal.fit("isotonic", "btts", p[:100], y[:100])
    a, b = protocol.slope_intercept(p, y)
    assert a == pytest.approx(0.5, rel=0.15)                          # the diagnostic sees the same distortion
    d = cal.fit("temperature", "btts", p, y).describe()
    assert d["checksum"] and d == cal.fit("temperature", "btts", p, y).describe()   # deterministic registry entry


def _rows_from(p, y, season, start):
    out = []
    for i in range(len(y)):
        po = float(p[i, 0])
        out.append({"league": "EPL", "season": season, "kickoff": (start + timedelta(hours=6 * i)).isoformat(), "y": int(y[i]), "o": int(y[i] == 0), "b": int(y[i] != 1),
                    "p": [float(x) for x in p[i]], "po": po, "pb": float(p[i, 0] + p[i, 2]), "low_confidence": False})  # fmt: skip
    return out


def test_the_protocol_promotes_a_clear_gain_and_the_test_window_never_changes_a_choice():
    rows = []
    for k, season in enumerate(("2020-21", "2021-22", "2022-23", "2023-24")):
        p, y, _ = _distorted(1500, 2.0, seed=10 + k)
        rows += _rows_from(p, y, season, datetime(2020 + k, 8, 1, tzinfo=UTC))
    res = protocol.select_and_test(rows)
    e = res["targets"]["1x2"]
    assert e["gate_passed"] and e["chosen"] != "identity" and e["status"] == "SHADOW_CANDIDATE" and e["test_paired"]["interval"][1] < 0
    flipped = [dict(r, y=(r["y"] + 1) % 3) if r["season"] == "2023-24" else r for r in rows]          # destroy the test labels
    res2 = protocol.select_and_test(flipped)
    assert res2["targets"]["1x2"]["chosen"] == e["chosen"] and res2["targets"]["1x2"]["select_window"] == e["select_window"]
    assert res2["targets"]["1x2"]["test_identity"] != e["test_identity"]                              # the test labels reach only the test metrics, never the choice


def test_the_real_excerpt_runs_the_protocol_and_keeps_the_windows_disjoint():
    rows = oof.read(REAL)
    assert len(rows) == 1000 and {r["season"] for r in rows} == {"2020-21", "2021-22", "2022-23", "2023-24"}
    for r in rows:
        assert abs(sum(r["p"]) - 1) < 1e-9 and 0 < r["po"] < 1 and 0 < r["pb"] < 1
    res = protocol.select_and_test(rows)
    sizes = {k: v["rows"] for k, v in res["windows"].items()}
    assert sizes == {"fit": 500, "select": 250, "test": 250}
    for t, e in res["targets"].items():
        assert e["status"] in ("KEEP_CHAMPION", "SHADOW_CANDIDATE") and e["registry"]
        assert set(e["select_window"]) >= set(cal.candidates(t))
    tiers = protocol.confidence_tiers(protocol.window(rows, "test"))
    assert sum(x["n"] for x in tiers) == 250


def test_final_test_seasons_are_refused_before_anything_is_loaded():
    with pytest.raises(ValueError, match="final-test seasons are locked"):
        oof.build_oof(ROOT, ("2023-24", "2024-25"))
    with pytest.raises(ValueError, match="final-test seasons are locked"):
        oof.build_oof(ROOT, ("2026-27",))


def test_tip_scoring_matches_the_90_minute_result():
    assert labels.tip_true({"market": "match_result", "pick": "Home win"}, "2-1") is True
    assert labels.tip_true({"market": "double_chance", "pick": "X2"}, "1-1") is True and labels.tip_true({"market": "double_chance", "pick": "1X"}, "0-2") is False
    assert labels.tip_true({"market": "goals_2_5", "pick": "Under 2.5 goals"}, "2-0") is True and labels.tip_true({"market": "btts", "pick": "Both teams score"}, "1-0") is False
    assert labels.tip_true({"market": "corners_9.5", "pick": "Over 9.5 corners"}, "1-0") is None


def test_label_reliability_on_the_real_forecast_excerpt(tmp_path):
    data = json.loads((ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json").read_text(encoding="utf-8"))
    rows = []
    for key, m in data["matches"].items():
        rows.append({"key": key, **m["result_row"]})
        d = tmp_path / "artifacts" / "markets" / key
        d.mkdir(parents=True)
        for f in m["intel"]:
            (d / f["file"]).write_text(json.dumps(f["body"]), encoding="utf-8")
    (tmp_path / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"matches": rows}), encoding="utf-8")
    out = labels.reliability(tmp_path)
    assert out["matches"] == 5 and out["cells"]
    for c in out["cells"]:
        assert c["label"] in labels.LABELS and 0 <= c["hit_rate"] <= 1 and c["ci95"][0] <= c["hit_rate"] <= c["ci95"][1] and c["verdict"] in ("CONSISTENT", "UNDERSTATES", "OVERSTATES")
