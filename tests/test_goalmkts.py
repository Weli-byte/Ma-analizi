"""Goal-market audit and live baselines (ADR 0088). The forecasts come from the REAL excerpt of five published 2026-10-09/10 artifacts (`real_oddsens_excerpt.json`); rows labelled TEST_ONLY are synthetic
and only exercise the arithmetic and the block rule."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.goalmkts import consistency, live, report

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "tests" / "fixtures" / "real_oddsens_excerpt.json"


@pytest.fixture(scope="module")
def excerpt():
    return json.loads(EXCERPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tree(tmp_path_factory, excerpt):
    root = tmp_path_factory.mktemp("goalmkts")
    rows = []
    for key, m in excerpt["matches"].items():
        rows.append({"key": key, **m["result_row"]})
        d = root / "artifacts" / "markets" / key
        d.mkdir(parents=True, exist_ok=True)
        for f in m["intel"]:
            (d / f["file"]).write_text(json.dumps(f["body"]), encoding="utf-8")
    (root / "artifacts" / "markets" / "track_record.json").write_text(json.dumps({"generated_at": excerpt["track_generated_at"], "matches": rows}), encoding="utf-8")
    return root


def test_labels_follow_the_90_minute_score_definition():
    assert live.labels("2-1") == (1, 1) and live.labels("1-1") == (0, 1) and live.labels("3-0") == (1, 0) and live.labels("0-0") == (0, 0) and live.labels("2-0") == (0, 0)


def test_every_real_published_artifact_satisfies_the_consistency_invariants(tree):
    out = consistency.audit(tree)
    assert out["artifacts"] > 5 and out["with_violation"] == 0 and out["unreadable"] == 0 and out["by_invariant"] == {}


def test_a_corrupted_artifact_is_flagged_not_repaired(excerpt):
    art = next(iter(excerpt["matches"].values()))["intel"][-1]["body"]
    assert consistency.check(art) == []
    cases = {
        "over_under_not_one": lambda a: [x.__setitem__("under", 0.9) for x in a["intelligence"]["goals"]["over_under"]],
        "btts_not_one": lambda a: a["intelligence"]["goals"].__setitem__("btts_no", 0.9),
        "sum_not_one:headline_probs": lambda a: a["intelligence"]["result"]["headline_probs"].__setitem__("home", 0.99),
        "scorelines_not_sorted": lambda a: a["intelligence"]["scorelines"].reverse(),
        "out_of_range:btts_yes": lambda a: a["intelligence"]["goals"].__setitem__("btts_yes", 1.2),
    }
    for expected, mutate in cases.items():
        broken = copy.deepcopy(art)
        mutate(broken)
        found = consistency.check(broken)
        assert any(expected.split(":")[0] in f for f in found), (expected, found)


def test_the_real_rows_carry_forecast_and_label_and_use_only_pre_kickoff_artifacts(tree):
    rows, rep = live.build_rows(tree)
    assert rep["settled"] == 5 and rep["used"] == 5 and len(rows) == 5
    for r in rows:
        assert 0 < r["p"]["over_2_5"] < 1 and 0 < r["p"]["btts"] < 1 and r["o"] in (0, 1) and r["b"] in (0, 1)
    assert [r["kickoff"] for r in rows] == sorted(r["kickoff"] for r in rows)


def _synthetic(n, p_over, rate_over):
    """TEST_ONLY rows: a constant forecast against labels with a given rate, spread over `n` consecutive days."""
    rng = np.random.default_rng(3)
    base = datetime(2026, 10, 1, 15, tzinfo=UTC)
    rows = []
    for i in range(n):
        o = int(rng.random() < rate_over)
        rows.append({"key": f"k{i}", "league": "EPL", "kickoff": base + timedelta(days=i), "o": o, "b": o, "p": {"over_2_5": p_over, "btts": p_over}, "ex": None})
    return rows


def test_prevalence_baselines_are_as_of_and_the_block_rule_blocks_short_samples():
    y = np.array([1.0, 1.0, 0.0, 1.0])
    pre = live._expanding(y)
    assert pre[0] == pytest.approx(0.5) and pre[1] == pytest.approx(2 / 3) and pre[2] == pytest.approx(3 / 4) and pre[3] == pytest.approx(3 / 5)         # before match i only matches < i are known
    short = live.evaluate(_synthetic(30, 0.6, 0.65))                                                                                                  # 30 days = five ISO weeks
    assert short["targets"]["over_2_5"]["paired_all_rows"]["production_vs_prevalence_history"]["verdict"] == "TOO_FEW_WEEKLY_BLOCKS"
    long_ = live.evaluate(_synthetic(120, 0.65, 0.65))
    p = long_["targets"]["over_2_5"]["paired_all_rows"]["production_vs_prevalence_history"]
    assert p["blocks"] >= live.MIN_BLOCKS and p["verdict"] in ("BETTER", "WORSE", "NO_DIFFERENCE_DEMONSTRATED") and live.evaluate(_synthetic(10, 0.6, 0.6))["status"] == "TOO_FEW_ROWS"


def test_the_report_states_the_label_contract_the_blocked_baseline_and_reads_the_stored_development_evidence(tree):
    rows, rep = live.build_rows(tree)
    many = rows * 6
    for i, r in enumerate(many):
        many[i] = {**r, "kickoff": r["kickoff"] + timedelta(days=i)}
    text = report.build(many, rep, live.evaluate(many), consistency.audit(tree), "test")
    assert "90-minute" in text and "BLOCKED" in text and "TOO_FEW_WEEKLY_BLOCKS" in text and "H_dc_hier" in text and "holdout untouched" in text
    assert "production_global" in text and "train_rate" in text
