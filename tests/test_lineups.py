"""Starting line-ups, information only (ADR 0081). The parser runs on a REAL ESPN summary (Athletic Club - Getafe, read 2026-10-25); the collector on synthetic scoreboard events with a fake directory
and fake fetchers (TEST_ONLY)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.markets import lineups
from src.web import i18n, pages
from tests.test_espn_fixtures import event

ROOT = Path(__file__).resolve().parents[1]
REAL = (ROOT / "tests" / "fixtures" / "real_espn_summary_lineups.json").read_bytes()
NOW = datetime(2026, 10, 25, 11, 30, tzinfo=UTC)


def test_the_real_summary_gives_two_complete_line_ups_with_formations_and_substitutes():
    r = lineups.parse_rosters(REAL)
    assert set(r) == {"home", "away"} and r["home"]["team"] == "Athletic Club" and r["away"]["team"] == "Getafe"
    assert r["home"]["formation"] == "4-2-3-1" and r["away"]["formation"] == "5-4-1"
    for side in r.values():
        assert len(side["starters"]) == 11 and all(p["name"] for p in side["starters"]) and len(side["substitutes"]) >= 7
    assert r["home"]["starters"][0]["name"] == "Unai Simón" and r["home"]["starters"][0]["position"] == "G"


def test_an_incomplete_or_missing_line_up_is_none_never_a_partial_one():
    data = json.loads(REAL)
    data["rosters"][1]["roster"] = [p for p in data["rosters"][1]["roster"] if not p["starter"]]               # one side has no starters yet
    assert lineups.parse_rosters(json.dumps(data).encode()) is None
    assert lineups.parse_rosters(json.dumps({"rosters": []}).encode()) is None and lineups.parse_rosters(b"{}") is None
    data2 = json.loads(REAL)
    data2["rosters"][0]["roster"] = [p for p in data2["rosters"][0]["roster"] if p["starter"]][:10]             # ten starters is not a line-up
    assert lineups.parse_rosters(json.dumps(data2).encode()) is None


class Dir:
    KNOWN = {"Athletic Club": "ESP_athletic_bilbao", "Getafe": "ESP_getafe"}

    def resolve_any_country(self, sources, name, day=None, country=None):
        class R:
            team_id = None

        r = R()
        r.team_id = self.KNOWN.get(name)
        assert "espn" in sources
        return r


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(lineups.TeamDirectory, "load", classmethod(lambda cls, p: Dir()))
    kick = NOW + timedelta(minutes=45)
    scoreboard = {"esp.1": [event(1, "Athletic Club", "Getafe", kick), event(2, "Mystery FC", "Getafe", kick), event(3, "Athletic Club", "Getafe", NOW + timedelta(hours=5)),
                            event(4, "Athletic Club", "Getafe", NOW - timedelta(hours=3), "STATUS_FULL_TIME", True, "1", "0")]}  # fmt: skip
    calls = []

    def fetch_day(slug, day, timeout=30.0):
        return scoreboard.get(slug, [])

    def fetch_sum(slug, event_id):
        calls.append(event_id)
        return REAL

    return tmp_path, fetch_day, fetch_sum, calls, kick


def test_only_matches_inside_the_window_are_looked_at_and_a_stored_line_up_is_immutable_and_idempotent(world):
    root, fd, fs, calls, kick = world
    out = lineups.collect(root, NOW, fd, fs)
    assert calls == ["1"]                                                       # not the unresolved club, not the match in five hours, not the finished one
    assert any("club not in the directory" in line for line in out) and any("line-up stored" in line for line in out)
    key = lineups.fixture_key("ESP_athletic_bilbao", "ESP_getafe", kick)
    files = sorted((root / "artifacts" / "lineups" / key).glob("lineup-*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["source"] == "espn" and "RESEARCH_ONLY" in body["source_terms"] and body["raw_sha256"] and body["lineup_sha256"] and body["observed_at"] == NOW.isoformat()
    again = lineups.collect(root, NOW + timedelta(minutes=10), fd, fs)
    assert any("unchanged" in line for line in again) and len(list((root / "artifacts" / "lineups" / key).glob("lineup-*.json"))) == 1


def test_a_changed_line_up_before_kick_off_is_a_new_file_and_a_failure_is_reported_not_filled(world):
    root, fd, fs, calls, kick = world
    lineups.collect(root, NOW, fd, fs)
    data = json.loads(REAL)
    data["rosters"][0]["roster"][0]["athlete"]["displayName"] = "Replacement GK"
    changed = json.dumps(data).encode()
    lineups.collect(root, NOW + timedelta(minutes=12), fd, lambda slug, ev: changed)
    key = lineups.fixture_key("ESP_athletic_bilbao", "ESP_getafe", kick)
    files = sorted((root / "artifacts" / "lineups" / key).glob("lineup-*.json"))
    assert len(files) == 2 and lineups.latest_for(root, "ESP_athletic_bilbao", "ESP_getafe", kick)["home"]["starters"][0]["name"] == "Replacement GK"

    def broken(slug, ev):
        raise RuntimeError("espn esp.1 1: URLError")

    out = lineups.collect(root, NOW + timedelta(minutes=24), fd, broken)
    assert any("FAILED" in line for line in out) and len(list((root / "artifacts" / "lineups" / key).glob("lineup-*.json"))) == 2
    pending = lineups.collect(root, NOW + timedelta(minutes=30), fd, lambda slug, ev: b'{"rosters": []}')
    assert any("no line-up published yet" in line for line in pending)


def test_old_folders_are_pruned_and_latest_for_is_none_without_a_file(world):
    root, fd, fs, calls, kick = world
    old = root / "artifacts" / "lineups" / "A__B__2024-10-01"
    old.mkdir(parents=True)
    (old / "lineup-x.json").write_text("{}", encoding="utf-8")
    lineups.collect(root, NOW, fd, fs)
    assert not old.exists()
    assert lineups.latest_for(root, "ESP_x", "ESP_y", kick) is None


INTEL = {"home": "Athletic Club", "away": "Getafe"}


def test_the_card_lists_the_starters_by_formation_place_and_says_plainly_when_nothing_is_announced():
    r = lineups.parse_rosters(REAL)
    html = pages.lineups_card(INTEL, {"observed_at": "2026-10-25T11:30:00+00:00", **r})
    assert "Kadrolar (bilgi amaçlı)" in html and "Unai Simón" in html and "4-2-3-1" in html and "Yedekler" in html and "yalnızca araştırma amaçlı" in html and "olasılıklarına girmez" in html
    assert html.index("Unai Simón") < html.index("Getafe")
    none = pages.lineups_card(INTEL, None)
    assert "Kadro henüz açıklanmadı" in none and "<ol" not in none
    en = i18n.translate_html(html)
    assert "Line-ups (information only)" in en and "Substitutes" in en and "Kadrolar" not in en


def test_the_match_page_shows_the_line_up_card_in_the_missing_players_tab_and_the_bundle_carries_it(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("mvb3", ROOT / "scripts" / "make_vercel_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = tmp_path / "p"
    d = fake / "artifacts" / "lineups" / "A__B__2026-10-25"
    d.mkdir(parents=True)
    (d / "lineup-20261025T100000Z.json").write_text("{}", encoding="utf-8")
    (d / "lineup-20261025T110000Z.json").write_text("{}", encoding="utf-8")
    for x in ("src", "configs"):
        (fake / x).mkdir()
        (fake / x / "f").write_text("", encoding="utf-8")
    (fake / "requirements-api.lock").write_text("a==1\n", encoding="utf-8")
    files = mod.build(tmp_path / "out", fake, now=datetime(2026, 10, 26, tzinfo=UTC))
    assert "artifacts/lineups/A__B__2026-10-25/lineup-20261025T110000Z.json" in files and "artifacts/lineups/A__B__2026-10-25/lineup-20261025T100000Z.json" not in files      # newest only
    src = (ROOT / "src" / "web" / "pages.py").read_text(encoding="utf-8")
    assert '"eksik": lineups_card(i, lineups) + injuries_card(i, injuries)' in src


def test_the_cloud_wiring_collects_in_every_shadow_round_and_publishes_to_both_data_branches():
    loop = (ROOT / "scripts" / "shadow_cloud_loop.sh").read_text(encoding="utf-8")
    assert "python -m src.markets.lineups" in loop and "artifacts/lineups" in loop and "round continues" in loop
    mk = (ROOT / ".github" / "workflows" / "markets-cloud.yml").read_text(encoding="utf-8")
    assert "git archive FETCH_HEAD artifacts/lineups" in mk and "markets-data-wt/artifacts/lineups" in mk
    assert "artifacts/lineups" in (ROOT / "src" / "markets" / "run.py").read_text(encoding="utf-8")
    assert "cp -r shadow-data-wt/artifacts/lineups" in (ROOT / ".github" / "workflows" / "shadow-cloud.yml").read_text(encoding="utf-8")
