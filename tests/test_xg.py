"""Real xG observations and their as-of safe features (ADR 0085). The rows are a REAL excerpt of the Highlightly store (all 270 EPL 2026-27 rows read on 2026-10-27, 59 with statistics);
synthetic rows below are labelled TEST_ONLY and only exercise the time rules."""

import json
import math
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.xg import features, observations, report
from src.xg.observations import MISSING_XG, NO_STATISTICS, OBSERVED, Observation

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "tests" / "fixtures" / "real_highlightly_xg_rows.jsonl"
T0 = datetime(2026, 10, 1, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    root = tmp_path_factory.mktemp("store")
    d = root / "artifacts" / "stats" / "highlightly"
    d.mkdir(parents=True)
    shutil.copy(REAL, d / "EPL_2026.jsonl")
    directory = TeamDirectory.load(config_dir_for(ROOT) / "team_aliases.yaml")
    obs, rep = observations.load(root, directory)
    return root, obs, rep


def test_the_real_rows_become_unique_observations_with_a_fetch_time_as_availability(real):
    _, obs, rep = real
    observed = [o for o in obs if o.coverage_status == OBSERVED]
    assert rep["unresolved_clubs"] == [] and rep["invalid_xg"] == 0 and rep["finished"] == len(obs) and len({o.source_match_id for o in obs}) == len(obs)
    assert len(observed) == 59 and all(o.xg_home >= 0 and o.xg_away >= 0 and o.published_at_utc is None for o in observed)
    assert all(o.available_at_utc == o.ingested_at_utc and o.available_at_utc > o.kickoff_utc for o in observed)    # usable only from the moment it was fetched
    assert all(o.available_at_utc is None and o.xg_home is None for o in obs if o.coverage_status != OBSERVED)       # never filled
    assert {o.coverage_status for o in obs} <= {OBSERVED, MISSING_XG, NO_STATISTICS}


def test_the_export_is_deterministic_and_the_digest_is_stable(real, tmp_path):
    _, obs, _ = real
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    observations.write_jsonl(obs, a)
    observations.write_jsonl(list(reversed(obs)), b)
    assert observations.digest(obs) == observations.digest(list(reversed(obs)))
    assert len(a.read_text(encoding="utf-8").splitlines()) == len(obs)
    observations.write_jsonl(obs, b)
    assert a.read_bytes() == b.read_bytes()


def _obs(i, home, away, xh, xa, kick, fetched, status=OBSERVED):
    """TEST_ONLY observation."""
    ok = status == OBSERVED
    return Observation("highlightly", str(i), f"hl-{i}", "EPL", 2026, kick, home, away, 1, 1, xh if ok else None, xa if ok else None, None, fetched if ok else None, fetched if ok else None, status, "t", f"c{i}")


def _season(fetch_delay=timedelta(hours=4)):
    """TEST_ONLY: team A plays five matches at 7-day steps; each xG is fetched `fetch_delay` after kick-off."""
    rows = []
    for i in range(5):
        k = T0 + timedelta(days=7 * i)
        rows.append(_obs(i, "A", f"O{i}", 1.0 + i, 2.0, k, k + fetch_delay) if i % 2 == 0 else _obs(i, f"O{i}", "A", 2.0, 1.0 + i, k, k + fetch_delay))
    return rows


def test_only_rows_fetched_before_the_cutoff_count_and_too_few_is_nan_with_a_reason():
    rows = _season()
    early = features.team_features(rows, "A", T0 + timedelta(days=15))              # matches 0,1,2 are fetched by then (2 only at +14d +4h)
    assert early["n_obs"] == 3 and early["xg_available"] and early["xg_for_r3"] == pytest.approx((1.0 + 2.0 + 3.0) / 3)
    few = features.team_features(rows, "A", T0 + timedelta(days=8))
    assert few["n_obs"] == 2 and not few["xg_available"] and math.isnan(few["xg_for_r3"]) and few["xg_missing_reason"] == "FEWER_THAN_3_OBSERVATIONS"
    none = features.team_features(rows, "A", T0 - timedelta(days=1))
    assert none["n_obs"] == 0 and none["xg_missing_reason"] == "NO_OBSERVATION_AVAILABLE_AT_CUTOFF" and none["last_available_at"] is None


def test_the_target_fixture_and_every_later_row_are_invisible_to_its_own_features():
    rows = _season()
    target = rows[3]
    cutoff = target.kickoff_utc - timedelta(minutes=90)
    f = features.fixture_features(rows, target.home_id, target.away_id, cutoff, target.fixture_key, force=True)
    assert f["away"]["n_obs"] == 3                                                  # matches 0,1,2 only
    late = _season(fetch_delay=timedelta(days=30))                                  # backfilled a month after kick-off: nothing was known at that time
    g = features.fixture_features(late, "A", "O3", late[3].kickoff_utc - timedelta(minutes=90), late[3].fixture_key, force=True)
    assert g["home"]["n_obs"] == 0 and not g["home"]["xg_available"]
    shuffled = features.team_features(list(reversed(rows)), "A", T0 + timedelta(days=30))
    assert shuffled == features.team_features(rows, "A", T0 + timedelta(days=30))   # idempotent, order independent


def test_missing_xg_rows_are_never_used_and_lower_the_coverage():
    rows = _season()
    rows[2] = _obs(2, "A", "O2", 0, 0, rows[2].kickoff_utc, rows[2].kickoff_utc, status=MISSING_XG)
    f = features.team_features(rows, "A", T0 + timedelta(days=40))
    assert f["n_obs"] == 4 and f["coverage_pct"] == pytest.approx(80.0)


def test_the_flag_is_off_by_default_and_force_is_explicit(monkeypatch):
    monkeypatch.delenv("XG_FEATURES_ENABLED", raising=False)
    rows = _season()
    assert not features.xg_features_enabled() and features.fixture_features(rows, "A", "O9", T0 + timedelta(days=40)) is None
    monkeypatch.setenv("XG_FEATURES_ENABLED", "1")
    assert features.fixture_features(rows, "A", "O9", T0 + timedelta(days=40))["feature_version"] == "xg-v1"


def test_real_data_features_pass_the_automatic_leakage_check_and_the_report_states_the_limits(real):
    _, obs, rep = real
    leak = report.leakage_check(obs)
    assert leak["checked"] > 0 and leak["own_row_available_before_own_cutoff"] == 0 and leak["future_rows_changed_a_feature"] == 0
    text = report.build(obs, rep, datetime(2026, 10, 27, tzinfo=UTC))
    assert "no backtest on earlier dates is possible" in text and "NOT MET - ablation not run" in text and "EPL" in text
    first = min(o.available_at_utc for o in obs if o.available_at_utc)
    cutoff_before = first - timedelta(days=1)
    g = features.fixture_features(obs, obs[-1].home_id, obs[-1].away_id, cutoff_before, force=True)
    assert not g["home"]["xg_available"] and not g["away"]["xg_available"]            # before the first fetch nothing is known, whatever the kick-off dates say


def test_report_is_a_function_of_data_and_as_of(real):
    _, obs, rep = real
    when = datetime(2026, 10, 27, tzinfo=UTC)
    assert report.build(obs, rep, when) == report.build(obs, rep, when)


def test_the_json_round_trip_of_an_observation_keeps_utc_times(real, tmp_path):
    _, obs, _ = real
    p = tmp_path / "o.jsonl"
    observations.write_jsonl(obs[:3], p)
    row = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert row["kickoff_utc"].endswith("+00:00") and row["source_id"] == "highlightly"


def test_the_leakage_check_really_detects_a_broken_availability_rule(real, monkeypatch):
    """Negative control: with the availability condition removed the automatic check must fire."""
    _, obs, _ = real

    def broken(rows, cutoff, exclude_key):
        return [o for o in rows if o.coverage_status == OBSERVED and o.kickoff_utc < cutoff and o.fixture_key != exclude_key]

    monkeypatch.setattr(features, "_eligible", broken)
    assert report.leakage_check(obs)["future_rows_changed_a_feature"] > 0


def test_the_baseline_rates_use_the_documented_trivial_strategies():
    """TEST_ONLY rows: five matches, the arithmetic of the baseline table."""
    from src.xg import baseline

    def row(i, result, over, pick_hit, ou_hit, btts_pick, btts_hit, score_hit=False):
        return {"key": f"k{i}", "date": f"2026-10-0{i}", "league": "EPL", "result": result, "over_actual": over, "pick_hit": pick_hit, "ou_hit": ou_hit, "btts_pick": btts_pick, "btts_hit": btts_hit, "score_hit": score_hit}

    rows = [row(1, "H", True, True, True, "Var", True), row(2, "A", False, False, False, "Var", False), row(3, "H", True, True, False, "Yok", False), row(4, "D", True, False, True, "Yok", True), row(5, "H", False, True, True, "Var", True, True)]
    text = baseline.build({"generated_at": "t", "matches": rows})
    assert "| 1X2 | 3/5 = 60.0 %" in text and "always home | 3/5 = 60.0 %" in text
    assert "always over | 3/5 = 60.0 %" in text and "always yes | 3/5 = 60.0 %" in text   # BTTS actual yes: rows 1, 3, 5 (Var & hit, Yok & miss)
    assert "| exact score | 1/5 = 20.0 %" in text and "First 138 matches" in text
