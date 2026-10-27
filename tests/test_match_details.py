"""Match detail tabs, live results and the track-record listing (ADR 0058)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config import load_config
from src.data.teams import TeamDirectory
from src.markets import details, live_results
from src.markets.data import STATS, StatMatch
from src.markets.track import hit_summary, wilson
from src.web import pages

ROOT = Path(__file__).resolve().parents[1]
EMPTY = dict.fromkeys(STATS)
NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def sm(i, league, day, h, a, hg, ag, season="2026-27"):
    ko = datetime(2026, 9, 1, 18, tzinfo=UTC) + timedelta(days=day)
    return StatMatch(f"m{i}", season, league, ko, ko + timedelta(hours=3), h, a, hg, ag, EMPTY, EMPTY, None)


HISTORY = [
    sm(1, "EPL", 0, "ENG_arsenal", "ENG_chelsea", 2, 0),
    sm(2, "EPL", 7, "ENG_chelsea", "ENG_arsenal", 1, 1),
    sm(3, "EPL", 14, "ENG_arsenal", "ENG_leeds_united", 3, 1),
    sm(4, "EPL", 21, "ENG_leeds_united", "ENG_chelsea", 0, 2),
    sm(5, "UCL", 28, "ENG_arsenal", "FRA_lille", 4, 0, "2026-27"),
    sm(6, "EPL", 60, "ENG_arsenal", "ENG_chelsea", 9, 9),  # after NOW: never visible
]
NAMES = {
    "ENG_arsenal": "Arsenal",
    "ENG_chelsea": "Chelsea",
    "ENG_leeds_united": "Leeds",
    "FRA_lille": "Lille",
}


def test_details_are_computed_only_from_results_available_at_the_cutoff():
    d = details.build_details(HISTORY, "ENG_arsenal", "ENG_chelsea", "EPL", NOW, NAMES)
    assert d["recent"]["home"][0]["opponent"] == "Lille" and d["recent"]["home"][0]["competition"] == "UCL"
    assert all(r["date"] <= "2026-10-14" for r in d["recent"]["home"] + d["recent"]["away"])
    assert [h["score"] for h in d["h2h"]] == [
        "1-1",
        "2-0",
    ]  # newest first, the 9-9 after the cutoff is absent
    t = d["table"]
    assert t["season"] == "2026-27" and t["rows"][0]["team"] == "Arsenal" and t["rows"][0]["pts"] == 7
    assert [r["pos"] for r in t["rows"]] == [1, 2, 3]
    assert (
        details.build_details(HISTORY, "ENG_arsenal", "FRA_lille", "UCL", NOW, NAMES)["table"] is None
    )  # a cup: no table
    assert details.league_table([], "EPL", NOW, NAMES) is None


def test_hit_summary_and_wilson_interval():
    assert hit_summary([]) == {"n": 0, "hits": 0, "rate": None, "ci95": None}
    s = hit_summary([True, True, False, True])
    assert s["hits"] == 3 and s["n"] == 4 and s["rate"] == 0.75
    lo, hi = wilson(3, 4)
    assert 0.3 < lo < 0.75 < hi < 0.99  # wide with four matches
    lo2, hi2 = wilson(300, 400)
    assert hi2 - lo2 < hi - lo


def test_live_results_parse_resolve_and_keep_the_first_seen_time():
    directory = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    payload = {
        "matches": [
            {
                "status": "FINISHED",
                "utcDate": "2026-10-10T14:00:00Z",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Chelsea FC"},
                "score": {"duration": "REGULAR", "fullTime": {"home": 2, "away": 1}},
            },  # fmt: skip
            {
                "status": "FINISHED",
                "utcDate": "2026-10-11T14:00:00Z",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Nobody FC"},
                "score": {"duration": "REGULAR", "fullTime": {"home": 1, "away": 0}},
            },  # fmt: skip
            {
                "status": "SCHEDULED",
                "utcDate": "2026-10-20T14:00:00Z",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Chelsea FC"},
                "score": {"fullTime": {"home": None, "away": None}},
            },  # fmt: skip
            {
                "status": "FINISHED",
                "utcDate": "2026-10-12T14:00:00Z",
                "homeTeam": {"name": "Arsenal FC"},
                "awayTeam": {"name": "Chelsea FC"},
                "score": {
                    "duration": "EXTRA_TIME",
                    "fullTime": {"home": 3, "away": 2},
                    "regularTime": {"home": 2, "away": 2},
                },
            },  # fmt: skip
        ]
    }
    rows, unresolved = live_results.from_football_data("EPL", payload, directory, NOW)
    assert unresolved == 1 and [(r["home_goals"], r["away_goals"]) for r in rows] == [
        (2, 1),
        (2, 2),
    ]  # 90-minute score
    store = {"league": "EPL", "matches": {}}
    assert live_results.merge(store, rows, NOW) == 2
    later = NOW + timedelta(hours=5)
    assert live_results.merge(store, rows, later) == 0  # nothing new
    assert {m["first_seen"] for m in store["matches"].values()} == {
        NOW.isoformat()
    }  # first observation is kept
    assert (
        live_results.score_90({"score": {"duration": "PENALTY_SHOOTOUT", "fullTime": {"home": 5, "away": 4}}})
        is None
    )


def test_odds_scores_use_league_phase_only_and_resolve_clubs():
    directory = TeamDirectory.load(ROOT / "configs" / "team_aliases.yaml")
    ev = {"completed": True, "commence_time": "2026-10-23T19:00:00Z", "home_team": "Arsenal", "away_team": "Lille", "scores": [{"name": "Arsenal", "score": "3"}, {"name": "Lille", "score": "0"}]}  # fmt: skip
    rows, unresolved = live_results.from_odds_scores("UCL", [ev], directory, NOW)
    assert len(rows) == 1 and rows[0]["home_goals"] == 3 and unresolved == 0
    knockout = {**ev, "commence_time": "2027-03-10T20:00:00Z"}
    assert live_results.from_odds_scores("UCL", [knockout], directory, NOW)[0] == []  # may include extra time
    assert live_results.from_odds_scores("UCL", [{**ev, "completed": False}], directory, NOW)[0] == []


def test_live_results_join_the_history_without_duplicates_and_stay_out_of_evaluations(tmp_path):
    from src.markets.of_source import live_matches

    cfg = load_config("markets")
    store = {
        "league": "EPL",
        "season": "2026-27",
        "matches": {
            "ENG_arsenal|ENG_chelsea|2026-10-10": {
                "league": "EPL",
                "home_id": "ENG_arsenal",
                "away_id": "ENG_chelsea",
                "kickoff_utc": "2026-10-10T14:00:00+00:00",
                "home_goals": 2,
                "away_goals": 1,
                "first_seen": "2026-10-14T09:00:00+00:00",
            },  # fmt: skip
            "ENG_chelsea|ENG_leeds_united|2026-10-11": {
                "league": "EPL",
                "home_id": "ENG_chelsea",
                "away_id": "ENG_leeds_united",
                "kickoff_utc": "2026-10-11T14:00:00+00:00",
                "home_goals": 0,
                "away_goals": 0,
                "first_seen": "2026-10-14T09:00:00+00:00",
            },  # fmt: skip
        },
    }
    (tmp_path / "artifacts" / "results").mkdir(parents=True)
    (tmp_path / "artifacts" / "results" / "EPL.json").write_text(json.dumps(store), encoding="utf-8")
    known = {
        ("ENG_arsenal", "ENG_chelsea", datetime(2026, 10, 10).date())
    }  # the open source already has this one
    blobs: list = []
    got = live_matches(tmp_path, known, cfg, NOW, blobs)
    assert len(got) == 1 and got[0].home_id == "ENG_chelsea" and got[0].home_goals == 0 and len(blobs) == 1
    assert got[0].available_at >= datetime.fromisoformat(
        "2026-10-14T09:00:00+00:00"
    )  # first seen, not kickoff


def _intel(details_block):
    res = {"headline_probs": {"home": 0.5, "draw": 0.25, "away": 0.25}, "headline_basis": "model", "market_probs_devig": None, "model_probs": {"home": 0.5, "draw": 0.25, "away": 0.25}, "double_chance": {"1X": 0.75, "12": 0.75, "X2": 0.5}, "draw_no_bet": {"home": 0.6, "away": 0.4}, "handicap_home_minus_1": 0.2, "handicap_away_minus_1": 0.1}  # fmt: skip
    goals = {"over_under": [{"line": 2.5, "over": 0.55, "under": 0.45}], "btts_yes": 0.5, "btts_no": 0.5, "home_over_1_5": 0.4, "away_over_1_5": 0.3, "home_clean_sheet": 0.3, "away_clean_sheet": 0.2, "expected": {"home": 1.5, "away": 1.1, "total": 2.6}, "total_goals_distribution": [0.1, 0.2, 0.2, 0.2, 0.1, 0.1, 0.05, 0.05]}  # fmt: skip
    return {
        "fixture_id": "A__B__2026-10-20",
        "league": "EPL",
        "kickoff_utc": "2026-10-20T14:00:00+00:00",
        "home": "Arsenal",
        "away": "Chelsea",
        "home_team_id": "ENG_arsenal",
        "away_team_id": "ENG_chelsea",
        "generated_at": "2026-10-14T10:00:00+00:00",
        "model_version": "m",
        "data_version": "d",
        "result": res,
        "goals": goals,
        "counts": {
            "corners": {
                "expected": {"home": 5, "away": 4, "total": 9.0},
                "most_likely_total": 9,
                "over_under": [{"line": 8.5, "over": 0.5, "under": 0.5}],
            }
        },  # fmt: skip
        "scorelines": [{"score": "1-1", "p": 0.12}, {"score": "2-1", "p": 0.1}],
        "tips": [],
        "data_quality": {"flags": []},
        "details": details_block,
        "neutral_venue": False,  # fmt: skip
    }


class Logos:
    def team_url(self, tid):
        return None

    def league_url(self, key):
        return None


def test_match_page_has_the_tabs_the_market_categories_form_table_and_injuries():
    d = details.build_details(HISTORY, "ENG_arsenal", "ENG_chelsea", "EPL", NOW, NAMES)
    html = pages.match_page(_intel(d), [], Logos(), None, {"observed_at": "2026-10-14T08:00:00+00:00", "home": [{"player": "B. Saka", "status": "INJURED", "reason": "Knock"}], "away": []})  # fmt: skip
    for needle in ("data-t='ozet'", "data-t='market'", "data-t='form'", "data-t='puan'", "data-t='eksik'", "Çifte şans", "Karşılıklı gol", "Korner", "Aralarındaki maçlar", "B. Saka"):  # fmt: skip
        assert needle in html, needle
    assert "class='me'" in html  # the two teams are highlighted in the table
    old = pages.match_page(_intel(None), [], Logos())
    assert (
        "ayrıntı istatistikleri eklenmeden önce" in old
    )  # an artifact from before this feature still renders
    cup = _intel({**d, "table": None})
    cup["league"] = "UCL"
    assert "kupası maçı" in pages.match_page(cup, [], Logos())
    stale = pages.stale_note([{"date": "2025-05-01"}], "2026-10-14T10:00:00+00:00")
    assert "2025" in stale or "01.05.25" in stale
    assert pages.stale_note([{"date": "2026-10-10"}], "2026-10-14T10:00:00+00:00") == ""


def test_track_page_lists_every_scored_match_with_hits_and_baselines():
    one = {"key": "k", "date": "2026-10-13", "league": "UCL", "home": "Arsenal", "away": "Lille", "score": "3-0", "result": "H", "pick": "H", "pick_prob": 0.7, "pick_hit": True, "market_driven": True, "score_pick": "2-0", "score_hit": False, "ou_pick": "Üst", "ou_hit": True, "btts_pick": "Yok", "btts_hit": True}  # fmt: skip
    t = {
        "available": True, "matches_scored": 2, "reliability_note": "noise: x",
        "headline_1x2": {"log_loss": 0.9, "log_loss_league_base_rate": 1.0},
        "tips_by_lean": {"lean": {"hit": 1, "n": 2, "rate": 0.5}},
        "hit_rates": {"1x2": hit_summary([True, False]), "over_under_2_5": hit_summary([True, True]), "btts": hit_summary([True, False]), "correct_score": hit_summary([False, False])},  # fmt: skip
        "naive_hit_rates": {"1x2_always_home": 0.5, "over_2_5_always_over": 0.7},
        "matches": [one, {**one, "key": "k2", "pick_hit": False}],
    }  # fmt: skip
    html = pages.track_page(t)
    assert "Arsenal - Lille" in html and "3-0" in html and "✓" in html and "✗" in html
    assert "ev sahibi kazanır" in html and "Tüm değerlendirilen maçlar" in html
    many = {**t, "matches": [{**one, "key": str(i)} for i in range(40)]}
    assert "Kalan 10 maçı gör" in pages.track_page(many)


def test_collect_runs_every_source_reports_failures_and_spends_credits_only_when_needed(
    tmp_path, monkeypatch
):
    import shutil

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    calls = {"fd": [], "odds": []}
    fd_payload = {"matches": [{"status": "FINISHED", "utcDate": "2026-10-10T14:00:00Z", "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Chelsea FC"}, "score": {"duration": "REGULAR", "fullTime": {"home": 2, "away": 1}}}]}  # fmt: skip

    def fake_get(path, key, params=None):
        calls["fd"].append(path)
        if "/DED/" in path:
            raise RuntimeError("boom")
        return fd_payload

    monkeypatch.setattr(live_results, "_get", fake_get)
    monkeypatch.setattr(live_results.time, "sleep", lambda s: None)
    monkeypatch.setattr(live_results, "odds_scores", lambda key, league: calls["odds"].append(league) or [])
    monkeypatch.setattr(live_results, "collect_espn", lambda *a, **k: [])  # the ESPN list is covered by tests/test_espn_fixtures.py; this test must not touch the network
    lines = live_results.collect(tmp_path, NOW, fd_key="k", odds_key="o")
    assert any(x.startswith("EPL: 1 finished, 1 new") for x in lines)
    assert any("NED1: football-data.org FAILED" in x for x in lines)
    assert all("no fixture waiting" in x for x in lines if x.startswith(("SUPERLIG", "UEL", "UECL")))
    assert calls["odds"] == []  # nothing is waiting: no credit
    # a stored fixture that kicked off 3 hours ago and has no result triggers exactly one scores call for its league
    meta = tmp_path / "artifacts" / "odds" / "oddsapi-x"
    meta.mkdir(parents=True)
    (meta / "meta.json").write_text(json.dumps({"fixture_id": "oddsapi-x", "league": "UEL", "home_id": "ENG_arsenal", "away_id": "ENG_chelsea", "kickoff_utc": (NOW - timedelta(hours=3)).isoformat()}), encoding="utf-8")  # fmt: skip
    assert live_results.pending_fixtures(tmp_path, "UEL", NOW) == 1
    live_results.collect(tmp_path, NOW, fd_key=None, odds_key="o")
    assert calls["odds"] == ["UEL"]
    none = live_results.collect(tmp_path, NOW, fd_key=None, odds_key=None)
    assert any("football-data.org: NOT_CONFIGURED" in x for x in none) and any(
        "scores: NOT_CONFIGURED" in x for x in none
    )
    failing = {"n": 0}

    def boom(key, league):
        failing["n"] += 1
        raise RuntimeError("the-odds-api scores failed: URLError")

    monkeypatch.setattr(live_results, "odds_scores", boom)
    assert any("scores failed" in x for x in live_results.collect(tmp_path, NOW, fd_key=None, odds_key="o"))
    stored = live_results.load_store(tmp_path, "EPL")
    assert stored["season"] == "2026-27" and len(stored["matches"]) == 1


def test_odds_scores_request_uses_the_scores_endpoint_and_never_leaks_the_key(monkeypatch):
    seen = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(live_results.urllib.request, "urlopen", lambda url, timeout: seen.append(url) or R())
    assert live_results.odds_scores("SECRETKEY", "SUPERLIG") == []
    assert "/sports/soccer_turkey_super_league/scores/" in seen[0] and "daysFrom=3" in seen[0]
    monkeypatch.setattr(live_results.urllib.request, "urlopen", lambda url, timeout: (_ for _ in ()).throw(live_results.urllib.error.URLError("SECRETKEY")))  # fmt: skip
    try:
        live_results.odds_scores("SECRETKEY", "UEL")
    except RuntimeError as e:
        assert "SECRETKEY" not in str(e)
    else:
        raise AssertionError("must raise")


def test_live_results_cli_prints_the_outcome(monkeypatch, capsys):
    monkeypatch.setattr(live_results, "configure_output", lambda: None)
    monkeypatch.setattr(live_results, "collect", lambda *a, **kw: ["EPL: ok"])
    assert live_results.main() == 0 and "EPL: ok" in capsys.readouterr().out


def test_track_record_lists_scored_matches_with_hit_flags(tmp_path):
    from src.markets.track import track_record

    base = tmp_path / "artifacts" / "markets" / "ENG_arsenal__ENG_chelsea__2026-10-10"
    base.mkdir(parents=True)
    probs = {"home": 0.6, "draw": 0.2, "away": 0.2}
    intel = {
        "result": {"headline_probs": probs, "model_probs": probs, "market_probs_devig": None},
        "goals": {"over_under": [{"line": 2.5, "over": 0.6, "under": 0.4}], "btts_yes": 0.4},
        "most_likely_score": "2-0", "tips": [{"market": "match_result", "pick": "Home win", "lean": "lean", "probability": 0.6}],
    }  # fmt: skip
    art = {"league": "EPL", "information_cutoff": "2026-10-09T10:00:00+00:00", "generated_at": "2026-10-09T10:00:00+00:00", "intelligence": intel}  # fmt: skip
    (base / "intel-1.json").write_text(json.dumps(art), encoding="utf-8")
    ko = datetime(2026, 10, 10, 14, tzinfo=UTC)
    played = StatMatch("r", "2026-27", "EPL", ko, ko + timedelta(hours=3), "ENG_arsenal", "ENG_chelsea", 2, 0, EMPTY, EMPTY, None)  # fmt: skip
    rep = track_record(tmp_path, [played], NOW, names=NAMES)
    m = rep["matches"][0]
    assert (
        rep["matches_scored"] == 1 and m["home"] == "Arsenal" and m["pick"] == "H" and m["pick_hit"] is True
    )
    assert m["score_hit"] is True and m["ou_pick"] == "Üst" and m["ou_hit"] is False and m["btts_hit"] is True
    assert rep["hit_rates"]["1x2"]["hits"] == 1 and rep["naive_hit_rates"]["1x2_always_home"] == 1.0


def test_scores_http_error_names_the_status_not_the_key(monkeypatch):
    import io

    def boom(url, timeout):
        raise live_results.urllib.error.HTTPError(
            url, 422, "x", {}, io.BytesIO(b'{"message": "Unprocessable"}')
        )

    monkeypatch.setattr(live_results.urllib.request, "urlopen", boom)
    try:
        live_results.odds_scores("SECRETKEY", "UEL")
    except RuntimeError as e:
        assert "HTTP 422 Unprocessable" in str(e) and "SECRETKEY" not in str(e)
    else:
        raise AssertionError("must raise")


def test_the_low_confidence_label_is_shown_on_the_card_and_the_match_page_and_translated():
    from src.web import i18n

    class NoLogos:
        def __getattr__(self, name):
            return lambda *a, **k: None

    low = _intel(None) | {"most_likely_score": "1-1"}
    low["data_quality"] = {"flags": ["home team has no history: neutral strength used"],
                           "low_confidence": {"level": "LOW", "reason": "NO_HISTORY", "label": "Geçmişi yok, düşük güven", "teams": ["ENG_arsenal"]}}  # fmt: skip
    card = pages.match_row(low, Logos())
    page = pages.match_page(low, [], Logos())
    assert card.count("Geçmişi yok, düşük güven") == 1 and "Geçmişi yok, düşük güven" in page and "lig ortalaması güçle" in page
    assert "No history, low confidence" in i18n.translate_html(card) and "league-average strength" in i18n.translate_html(page)
    plain = _intel(None) | {"most_likely_score": "1-1"}
    assert "Geçmişi yok" not in pages.match_row(plain, Logos()) and "Geçmişi yok" not in pages.match_page(plain, [], Logos())   # artifacts written before this field exist


def test_the_match_card_shows_expected_goals_next_to_the_most_likely_score_and_its_probability():
    from src.web import i18n

    class Logos:
        def __getattr__(self, name):
            return lambda *a, **k: None

    intel = _intel(None) | {"most_likely_score": "1-1", "goals": {"expected": {"home": 1.64, "away": 1.08, "total": 2.72}}, "scorelines": [{"score": "1-1", "p": 0.112}]}
    card = pages.match_row(intel, Logos())
    assert "Beklenen gol" in card and "1.6 - 1.1" in card and "En olası skor" in card and "(%11)" in card
    assert "Expected goals" in i18n.translate_html(card)
