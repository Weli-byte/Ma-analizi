"""Referee and player information (ADR 0059): parsed from football-data.org payload shapes, shown as information only."""

import json
from datetime import UTC, datetime
from pathlib import Path

from src.data.teams import TeamDirectory
from src.markets import live_results, people
from src.web import pages

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
REF = [{"id": 1, "name": "Anthony Taylor", "type": "REFEREE", "nationality": "England"}, {"id": 2, "name": "Gary Beswick", "type": "ASSISTANT_REFEREE_N1"}]  # fmt: skip


def directory():
    return TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")


def test_referee_upcoming_fixtures_and_scorers_are_parsed_and_resolved():
    payload = {
        "matches": [
            {
                "id": 9,
                "status": "FINISHED",
                "utcDate": "2026-10-04T14:00:00Z",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Chelsea FC"},
                "score": {"duration": "REGULAR", "fullTime": {"home": 3, "away": 1}},
                "referees": REF,
            },  # fmt: skip
            {
                "id": 10,
                "status": "TIMED",
                "utcDate": "2026-10-18T14:00:00Z",
                "homeTeam": {"name": "Chelsea FC"},
                "awayTeam": {"name": "Arsenal FC"},
                "referees": REF,
            },  # fmt: skip
            {
                "id": 11,
                "status": "SCHEDULED",
                "utcDate": "2026-10-19T14:00:00Z",
                "homeTeam": {"name": "Nobody FC"},
                "awayTeam": {"name": "Arsenal FC"},
                "referees": [],
            },  # fmt: skip
        ]
    }
    rows, _ = live_results.from_football_data("EPL", payload, directory(), NOW)
    assert rows[0]["referee"] == {"name": "Anthony Taylor", "nationality": "England"}
    up = live_results.upcoming_from_football_data("EPL", payload, directory(), NOW)
    assert [u["fixture_id"] for u in up] == ["fd-10"] and up[0]["referee"]["name"] == "Anthony Taylor"
    assert live_results.referee_of({"referees": [{"type": "ASSISTANT_REFEREE_N1", "name": "x"}]}) is None
    sc = live_results.scorers_from_football_data("EPL", {"scorers": [{"player": {"name": "B. Saka", "section": "Offence"}, "team": {"name": "Arsenal FC"}, "goals": 5, "assists": 2, "penalties": None, "playedMatches": 7}]}, directory())  # fmt: skip
    assert sc[0]["team_id"] == "ENG_arsenal" and sc[0]["goals"] == 5 and sc[0]["played"] == 7
    store = {"matches": {}}
    live_results.merge(store, [{**rows[0], "referee": None}], NOW)
    live_results.merge(store, rows, NOW)  # a referee learned later is added; the result itself never changes
    assert next(iter(store["matches"].values()))["referee"]["name"] == "Anthony Taylor"


def _store(tmp_path):
    st = {
        "league": "EPL",
        "matches": {
            "a": {
                "home_id": "ENG_arsenal",
                "away_id": "ENG_chelsea",
                "kickoff_utc": "2026-10-04T14:00:00+00:00",
                "home_goals": 3,
                "away_goals": 1,
                "referee": {"name": "Anthony Taylor"},
            },  # fmt: skip
            "b": {
                "home_id": "ENG_leeds_united",
                "away_id": "ENG_fulham",
                "kickoff_utc": "2026-09-27T14:00:00+00:00",
                "home_goals": 0,
                "away_goals": 0,
                "referee": {"name": "Anthony Taylor"},
            },  # fmt: skip
            "c": {
                "home_id": "ENG_everton",
                "away_id": "ENG_fulham",
                "kickoff_utc": "2026-09-20T14:00:00+00:00",
                "home_goals": 1,
                "away_goals": 2,
                "referee": {"name": "Other"},
            },  # fmt: skip
        },
        "upcoming": [
            {
                "fixture_id": "fd-10",
                "home_id": "ENG_chelsea",
                "away_id": "ENG_arsenal",
                "kickoff_utc": "2026-10-18T14:00:00+00:00",
                "referee": {"name": "Anthony Taylor", "nationality": "England"},
            }
        ],  # fmt: skip
        "scorers": [
            {
                "player": "B. Saka",
                "team_id": "ENG_arsenal",
                "goals": 5,
                "assists": 2,
                "penalties": None,
                "played": 7,
            },
            {
                "player": "C. Palmer",
                "team_id": "ENG_chelsea",
                "goals": 4,
                "assists": None,
                "penalties": 2,
                "played": 7,
            },
        ],
    }
    (tmp_path / "artifacts" / "results").mkdir(parents=True)
    (tmp_path / "artifacts" / "results" / "EPL.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "artifacts" / "results" / "BROKEN.json").write_text("{not json", encoding="utf-8")


def test_people_for_an_upcoming_fixture(tmp_path):
    _store(tmp_path)
    p = people.people_for(tmp_path, "ENG_chelsea", "ENG_arsenal", datetime(2026, 10, 18, 14, tzinfo=UTC))
    assert p["referee"]["name"] == "Anthony Taylor"
    st = p["referee_stats"]
    assert st["matches"] == 2 and st["avg_goals"] == 2.0 and st["home_win"] == 0.5 and st["draw"] == 0.5
    assert (
        p["scorers"]["home"][0]["player"] == "C. Palmer" and p["scorers"]["away"][0]["competition"] == "EPL"
    )
    none = people.people_for(tmp_path, "ENG_everton", "ENG_fulham", NOW)
    assert none["referee"] is None and none["referee_stats"] is None
    assert people.referee_stats(tmp_path, "Nobody")["matches"] == 0


def test_referee_and_player_tabs_render_with_and_without_data(tmp_path):
    _store(tmp_path)
    p = people.people_for(tmp_path, "ENG_chelsea", "ENG_arsenal", datetime(2026, 10, 18, 14, tzinfo=UTC))
    names = {
        "ENG_arsenal": "Arsenal",
        "ENG_chelsea": "Chelsea",
        "ENG_leeds_united": "Leeds",
        "ENG_fulham": "Fulham",
    }
    html = pages.referee_tab(p, names)
    assert (
        "Anthony Taylor" in html
        and "Maç başı gol" in html
        and "Arsenal - Chelsea" in html
        and "tahmine girmez" in html
    )
    assert "henüz açıklanmadı" in pages.referee_tab(None)
    lonely = {"referee": {"name": "New Ref"}, "referee_stats": {"matches": 0}}
    assert "yönettiği maç yok" in pages.referee_tab(lonely)
    i = {"home": "Chelsea", "away": "Arsenal"}
    tab = pages.players_tab(i, p)
    assert "C. Palmer" in tab and "B. Saka" in tab and "football-data.org" in tab
    assert "gol krallığı verisi yok" in pages.players_tab(i, None)


def test_crests_are_collected_downloaded_from_the_crest_host_only_and_served(tmp_path, monkeypatch):
    from src.markets import alias_logos
    from src.web import logos as web_logos

    payload = {"matches": [{"homeTeam": {"name": "Arsenal FC", "crest": "https://crests.football-data.org/57.png"}, "awayTeam": {"name": "Nobody FC", "crest": "https://crests.football-data.org/1.png"}, "competition": {"emblem": "https://crests.football-data.org/PL.png"}}]}  # fmt: skip
    crests = live_results.crests_from_football_data("EPL", payload, directory())
    assert crests == {"ENG_arsenal": "https://crests.football-data.org/57.png"}
    st = {"league": "EPL", "crests": {**crests, "ENG_chelsea": "https://evil.example/x.png"}, "emblem": "https://crests.football-data.org/PL.png"}  # fmt: skip
    (tmp_path / "artifacts" / "results").mkdir(parents=True)
    (tmp_path / "artifacts" / "results" / "EPL.json").write_text(json.dumps(st), encoding="utf-8")
    fetched = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"\x89PNG fake"

    monkeypatch.setattr(
        live_results.urllib.request, "urlopen", lambda req, timeout: fetched.append(req.full_url) or R()
    )
    msg = live_results.download_crests(tmp_path)
    assert "2 downloaded" in msg and all(u.startswith("https://crests.football-data.org/") for u in fetched)
    assert (tmp_path / "artifacts" / "crests" / "ENG_arsenal.png").exists()
    assert not (
        tmp_path / "artifacts" / "crests" / "ENG_chelsea.png"
    ).exists()  # another host is never fetched
    assert "0 downloaded" in live_results.download_crests(tmp_path)  # nothing twice
    monkeypatch.setenv("FORECAST_UI_LOGOS", "1")
    idx = web_logos.LogoIndex(tmp_path)
    assert idx.team("ENG_arsenal").name == "ENG_arsenal.png" and idx.league("EPL").name == "league_EPL.png"
    assert idx.team_url("ENG_arsenal") == "/logo/team/ENG_arsenal" and idx.league("UEL") is None
    assert alias_logos.spellings("FK Bodø/Glimt")[-1] == "FK Bodo/Glimt" and "Porto" in alias_logos.spellings(
        "FC Porto"
    )


def test_logo_files_are_served_sandboxed_so_an_svg_cannot_run_script(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    (tmp_path / "artifacts" / "crests").mkdir(parents=True)
    (tmp_path / "artifacts" / "crests" / "ENG_arsenal.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", encoding="utf-8")  # fmt: skip
    monkeypatch.setenv("FORECAST_UI_LOGOS", "1")
    r = TestClient(create_app(tmp_path, keys=[])).get("/logo/team/ENG_arsenal")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert (
        "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    )
