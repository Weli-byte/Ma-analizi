"""Starting line-ups with an as-of contract (ADR 0086). The files are REAL ESPN line-ups stored by the cloud collector (11 fixtures, 19 files, 2026-10-26 to 2026-10-28); the multi-match
chains below are labelled TEST_ONLY because the real store only starts on 2026-10-25 and holds one match per club so far."""

import json
import math
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.availability import features, lineup_asof, report
from src.availability.lineup_asof import CONFIRMED, UNKNOWN, LineupObservation

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "tests" / "fixtures" / "real_espn_lineups"


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("lineups")
    shutil.copytree(REAL, root / "artifacts" / "lineups")
    return root


@pytest.fixture(scope="module")
def loaded(store):
    return lineup_asof.load(store)


def test_the_real_files_become_confirmed_observations_with_eleven_distinct_starters(loaded):
    obs, rep = loaded
    assert rep["files"] == 19 and rep["quarantined"] == 0 and len(obs) == 19 and len({o.fixture_key for o in obs}) == 11
    assert all(o.lineup_status == CONFIRMED and o.source_published_at_utc is None and o.source_id == "espn" for o in obs)
    assert all(len(set(o.starters["home"])) == 11 and len(set(o.starters["away"])) == 11 and o.goalkeepers["home"] and o.goalkeepers["away"] for o in obs)
    assert all(k.split("|")[0] in (o.home_id, o.away_id) for o in obs for side in ("home", "away") for k in o.starters[side])


def test_a_sighting_after_kick_off_is_kept_but_never_enters_a_pre_match_snapshot(loaded):
    obs, _ = loaded
    psg = [o for o in obs if o.fixture_key.startswith("FRA_paris_saint_germain")]
    assert len(psg) == 2 and [o.pre_kickoff for o in sorted(psg, key=lambda o: o.observed_at_utc)] == [True, False]   # the second sighting is nine minutes after kick-off
    kick = psg[0].kickoff_utc
    assert lineup_asof.snapshot_at(psg, kick + timedelta(hours=1)).observed_at_utc < kick                           # even a late cutoff only sees the pre-kick-off XI
    assert lineup_asof.snapshot_at(psg, kick - timedelta(hours=3)) is None                                           # nothing was seen three hours before: UNKNOWN, not guessed


def test_stage_snapshots_are_deterministic_unknown_when_unseen_and_never_overwritten(loaded, tmp_path):
    obs, _ = loaded
    v = lineup_asof.by_fixture(obs)["ESP_sevilla__ESP_osasuna__2026-10-26"]
    a, b = lineup_asof.stage_snapshots(v), lineup_asof.stage_snapshots(list(reversed(v)))
    assert a == b and a["t-24h"]["lineup_status"] == UNKNOWN and a["t-24h"]["starters"] is None
    assert a["t-30m"]["lineup_status"] == CONFIRMED and a["t-30m"]["observed_at_utc"] <= a["t-30m"]["cutoff_utc"]
    now = max(o.kickoff_utc for o in obs) + timedelta(hours=1)
    first = lineup_asof.write_snapshots(tmp_path, obs, now)
    again = lineup_asof.write_snapshots(tmp_path, obs, now)
    assert first["written"] == 33 and first["conflicts"] == [] and again["written"] == 0 and again["unchanged"] == 33
    victim = next((tmp_path / "artifacts" / "lineup_snapshots").glob("*/t-30m.json"))
    victim.write_text("{}", encoding="utf-8")
    third = lineup_asof.write_snapshots(tmp_path, obs, now)
    assert len(third["conflicts"]) == 1 and victim.read_text(encoding="utf-8") == "{}"                              # reported, not overwritten
    early = lineup_asof.write_snapshots(tmp_path / "early", obs, min(o.kickoff_utc for o in obs) - timedelta(days=5))
    assert early["written"] == 0 and early["not_due"] == 33                                                          # a stage whose cutoff has not passed is not written


def test_a_file_without_eleven_starters_is_quarantined_not_repaired(store, tmp_path):
    root = tmp_path / "q"
    shutil.copytree(store / "artifacts" / "lineups", root / "artifacts" / "lineups")
    f = next((root / "artifacts" / "lineups").glob("*/lineup-*.json"))
    body = json.loads(f.read_text(encoding="utf-8"))
    body["lineups"]["home"]["starters"] = body["lineups"]["home"]["starters"][:10]
    f.write_text(json.dumps(body), encoding="utf-8")
    (root / "artifacts" / "lineups" / "X__Y__2026-10-27").mkdir()
    (root / "artifacts" / "lineups" / "X__Y__2026-10-27" / "lineup-bad.json").write_text("{not json", encoding="utf-8")
    obs, rep = lineup_asof.load(root)
    assert rep["quarantined"] == 2 and len(obs) == 18 and rep["quarantine_reasons"]["NOT_ELEVEN_DISTINCT_STARTERS"] == 1


def test_the_provisional_player_key_ignores_accents_case_and_punctuation():
    assert lineup_asof.player_key("ESP_x", "Álvaro  García-Pérez") == lineup_asof.player_key("ESP_x", "alvaro garcia perez") == "ESP_x|alvaro garcia perez"
    assert lineup_asof.player_key("ESP_x", "A") != lineup_asof.player_key("ESP_y", "A")


def _chain(n, change_at=None, gk_change_at=None):
    """TEST_ONLY: club C plays n fixtures a week apart against distinct opponents; its XI is p0..p10 (GK p0) except that at `change_at` players p3 and p4 are replaced and at `gk_change_at` the keeper."""
    out = []
    for i in range(n):
        kick = datetime(2026, 11, 1, 15, tzinfo=UTC) + timedelta(days=7 * i)
        xi = [f"p{j}" for j in range(11)]
        if change_at == i:
            xi[3], xi[4] = "q3", "q4"
        gk = "p0" if gk_change_at != i else "q0"
        if gk_change_at == i:
            xi[0] = "q0"
        keys = tuple(lineup_asof.player_key("C", x) for x in xi)
        opp = tuple(lineup_asof.player_key(f"O{i}", f"o{j}") for j in range(11))
        out.append(LineupObservation(f"C__O{i}__{kick.date()}", "EPL", kick, "C", f"O{i}", "espn", str(i), kick - timedelta(minutes=50), None, CONFIRMED, True, {"home": "4-4-2" if change_at != i else "3-5-2", "away": "4-3-3"},
                                     {"home": keys, "away": opp}, {"home": lineup_asof.player_key("C", gk), "away": opp[0]}, {"home": 9, "away": 9}, f"h{i}", f"c{i}"))  # fmt: skip
    return out


def test_features_compare_with_the_previous_line_up_and_need_three_earlier_ones_for_the_usual_xi():
    obs = _chain(6, change_at=4, gk_change_at=5)
    cutoff = lambda i: obs[i].kickoff_utc - timedelta(minutes=30)  # noqa: E731
    f0 = features.fixture_features(obs, obs[0].fixture_key, cutoff(0), force=True)["home"]
    assert f0["lineup_status"] == CONFIRMED and math.isnan(f0["starters_changed_n"]) and f0["formation_change"] is None and math.isnan(f0["usual_xi_missing_n"])      # no earlier line-up
    f2 = features.fixture_features(obs, obs[2].fixture_key, cutoff(2), force=True)["home"]
    assert f2["starters_changed_n"] == 0 and f2["formation_change"] is False and f2["goalkeeper_change"] is False and math.isnan(f2["usual_xi_missing_n"])           # two earlier: usual XI not yet defined
    f3 = features.fixture_features(obs, obs[3].fixture_key, cutoff(3), force=True)["home"]
    assert f3["usual_xi_missing_n"] == 0.0
    f4 = features.fixture_features(obs, obs[4].fixture_key, cutoff(4), force=True)["home"]
    assert f4["starters_changed_n"] == 2 and f4["formation_change"] is True and f4["usual_xi_missing_n"] == 2.0 and f4["bench_size"] == 9.0
    f5 = features.fixture_features(obs, obs[5].fixture_key, cutoff(5), force=True)["home"]
    assert f5["goalkeeper_change"] is True and f5["starters_changed_n"] == 3                                                                                         # q0 in, q3 and q4 come back out of the changed XI


def test_features_never_use_a_line_up_seen_after_the_cutoff_and_say_unknown_when_nothing_was_seen():
    obs = _chain(4)
    late = [replace(o, observed_at_utc=o.kickoff_utc - timedelta(minutes=10)) if i == 3 else o for i, o in enumerate(obs)]
    f = features.fixture_features(late, late[3].fixture_key, late[3].kickoff_utc - timedelta(minutes=30), force=True)
    assert f["lineup_status"] == UNKNOWN and f["home"]["missing_reason"] == "NO_CONFIRMED_LINEUP_AT_CUTOFF" and math.isnan(f["home"]["bench_size"])
    assert features.fixture_features(obs, "nope", obs[0].kickoff_utc, force=True)["missing_reason"] == "FIXTURE_NOT_OBSERVED"


def test_the_flag_is_off_by_default_and_force_is_explicit(monkeypatch):
    obs = _chain(2)
    monkeypatch.delenv("LINEUP_FEATURES_ENABLED", raising=False)
    assert not features.lineup_features_enabled() and features.fixture_features(obs, obs[1].fixture_key, obs[1].kickoff_utc) is None
    monkeypatch.setenv("LINEUP_FEATURES_ENABLED", "1")
    assert features.fixture_features(obs, obs[1].fixture_key, obs[1].kickoff_utc)["feature_version"] == "lineup-v1"


def test_real_data_passes_the_automatic_leakage_check_and_a_broken_rule_is_caught(loaded, monkeypatch):
    obs, rep = loaded
    leak = report.leakage_check(obs)
    assert leak["checked"] == 33 and leak["snapshot_after_cutoff_or_post_kickoff"] == 0 and leak["features_changed_by_later_observations"] == 0
    chain = _chain(5)
    assert report.leakage_check(chain)["features_changed_by_later_observations"] == 0
    monkeypatch.setattr(features, "snapshot_at", lambda versions, cutoff: max(versions, key=lambda o: o.observed_at_utc))   # negative control: the cutoff is ignored
    monkeypatch.setattr(report, "snapshot_at", features.snapshot_at)
    assert report.leakage_check(chain)["features_changed_by_later_observations"] > 0 or report.leakage_check(chain)["snapshot_after_cutoff_or_post_kickoff"] > 0


def test_the_report_states_the_censoring_the_missing_denominator_and_the_gate(loaded):
    obs, rep = loaded
    text = report.build(obs, rep, datetime(2026, 10, 29, tzinfo=UTC))
    assert "UPPER bound" in text and "denominator is NOT stored" in text and "NOT MET - ablation not run" in text and "Player impact" in text
    assert report.build(obs, rep, datetime(2026, 10, 29, tzinfo=UTC)) == text
