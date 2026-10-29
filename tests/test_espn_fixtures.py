"""ESPN scoreboard as a free fixture / result list for the Europa and Conference League (ADR 0070).

The event payload below has the real field layout of `site.api.espn.com/.../uefa.europa/scoreboard` (read 2026-10-22); clubs in the directory fake are TEST_ONLY."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from src.ingestion import espn_fixtures as ef
from src.ingestion.provider import ProviderError
from src.markets import live_results as lr

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def event(i, home, away, when, status="STATUS_SCHEDULED", completed=False, hs="0", as_="0"):
    return {"id": str(i), "date": when.strftime("%Y-%m-%dT%H:%MZ"), "status": {"type": {"name": status, "completed": completed}},
            "competitions": [{"competitors": [{"homeAway": "home", "score": hs, "team": {"displayName": home}}, {"homeAway": "away", "score": as_, "team": {"displayName": away}}]}]}  # fmt: skip


def test_an_event_is_parsed_into_a_match_and_a_broken_one_is_dropped_not_guessed():
    m = ef.parse_event(event(1, "Juventus", "Stade Rennais", datetime(2026, 10, 22, 16, 45, tzinfo=UTC), "STATUS_FULL_TIME", True, "2", "1"))
    assert m["home"] == "Juventus" and m["away"] == "Stade Rennais" and m["completed"] and (m["home_goals"], m["away_goals"]) == (2, 1) and m["kickoff_utc"].tzinfo is not None
    sched = ef.parse_event(event(2, "A", "B", NOW + timedelta(days=3)))
    assert sched["completed"] is False and sched["home_goals"] is None            # a scheduled match has no score, never 0-0
    assert ef.parse_event({"id": "3"}) is None and ef.parse_event({"id": "4", "date": "x", "competitions": []}) is None


def test_the_scan_goes_day_by_day_deduplicates_and_reports_failed_days(monkeypatch):
    calls = []

    def fetch(slug, day, timeout=30.0):
        calls.append(day)
        if day == date(2026, 10, 24):
            raise ProviderError("espn boom")
        return [event(7, "A", "B", datetime(2026, 10, 23, 17, tzinfo=UTC))]

    monkeypatch.setattr(ef, "fetch_day", fetch)
    matches, errors = ef.scan("uefa.europa", date(2026, 10, 22), date(2026, 10, 25), delay=0)
    assert len(calls) == 4 and len(matches) == 1 and errors == ["espn boom"]       # one request per day; the repeated event id is kept once


class Dir:
    KNOWN = {"Juventus": "ITA_juventus", "Stade Rennais": "FRA_rennes", "Ajax Amsterdam": "NED_afc_ajax", "Celtic": "SCO_celtic_fc"}

    def resolve_any_country(self, sources, name, day=None, country=None):
        class R:
            team_id = None

        r = R()
        r.team_id = self.KNOWN.get(name)
        assert "espn" in sources                      # approved ESPN aliases are consulted
        return r


def test_results_and_upcoming_are_separated_unresolved_clubs_are_reported_and_knockout_scores_are_refused():
    ms = [
        ef.parse_event(event(1, "Juventus", "Stade Rennais", datetime(2026, 10, 22, 16, 45, tzinfo=UTC), "STATUS_FULL_TIME", True, "2", "1")),
        ef.parse_event(event(2, "Ajax Amsterdam", "Celtic", datetime(2026, 11, 5, 17, 45, tzinfo=UTC))),
        ef.parse_event(event(3, "Mystery FC", "Celtic", datetime(2026, 11, 5, 17, 45, tzinfo=UTC))),
        ef.parse_event(event(4, "Juventus", "Celtic", datetime(2027, 3, 12, 17, 45, tzinfo=UTC), "STATUS_FULL_TIME", True, "1", "1")),   # knockout month: could include extra time
        ef.parse_event(event(5, "Juventus", "Celtic", datetime(2026, 11, 6, 17, 45, tzinfo=UTC), "STATUS_POSTPONED")),                  # postponed: not an upcoming fixture
    ]
    res, up, unresolved = lr.from_espn("UEL", ms, Dir(), datetime(2027, 4, 1, tzinfo=UTC))
    assert [(r["home_id"], r["home_goals"], r["away_goals"]) for r in res] == [("ITA_juventus", 2, 1)] and unresolved == ["Mystery FC"]
    res2, up2, _ = lr.from_espn("UEL", ms, Dir(), NOW)
    assert [(u["fixture_id"], u["home_id"], u["away_id"]) for u in up2] == [("espn-2", "NED_afc_ajax", "SCO_celtic_fc")] and len(res2) == 1
    assert res2[0]["source"].startswith("espn") and up == []                  # at the 2027 clock everything is in the past


def test_collect_espn_keeps_other_sources_upcoming_and_replaces_only_its_own(tmp_path):
    root = tmp_path
    store = {"league": "UEL", "matches": {}, "upcoming": [{"fixture_id": "odds-9", "home_id": "ITA_juventus", "away_id": "FRA_rennes", "kickoff_utc": "2026-11-05T17:45:00+00:00"},
                                                        {"fixture_id": "espn-old", "home_id": "X", "away_id": "Y", "kickoff_utc": "2026-10-30T17:45:00+00:00"}]}  # fmt: skip
    lr.save_store(root, "UEL", store)
    lr.save_store(root, "UECL", {"league": "UECL", "matches": {}})

    def scanner(slug, start, end):
        return [ef.parse_event(event(2, "Ajax Amsterdam", "Celtic", datetime(2026, 11, 5, 17, 45, tzinfo=UTC))),
                ef.parse_event(event(3, "Juventus", "Stade Rennais", datetime(2026, 11, 5, 17, 45, tzinfo=UTC)))], []  # fmt: skip

    lines = lr.collect_espn(root, NOW, Dir(), scanner=scanner)
    saved = json.loads(lr.results_path(root, "UEL").read_text(encoding="utf-8"))
    ids = [u["fixture_id"] for u in saved["upcoming"]]
    assert "odds-9" in ids and "espn-2" in ids and "espn-old" not in ids and "espn-3" not in ids     # a duplicate of an odds-store fixture is not listed twice
    assert len(lines) == 2 and "UEL: espn 2 matches" in lines[0] and saved["espn_observed_at"] == NOW.isoformat()
    again = lr.collect_espn(root, NOW, Dir(), scanner=scanner)
    assert json.loads(lr.results_path(root, "UEL").read_text(encoding="utf-8"))["upcoming"] == saved["upcoming"] and again


def test_the_slugs_are_the_verified_ones():
    assert ef.SLUGS["UEL"] == "uefa.europa" and ef.SLUGS["UECL"] == "uefa.europa.conf" and pytest.importorskip("src.markets.live_results").ESPN_LEAGUES == ("UEL", "UECL")


def test_settlement_adds_missing_domestic_results_only_and_never_changes_a_stored_one(tmp_path):
    """ADR 0090: results only, all six domestic leagues; a stored result stands; the fixture list is not touched; a spring league match decided in 90 minutes is accepted."""
    root = tmp_path
    for league in (*lr.SETTLE_LEAGUES, *lr.SETTLE_CUPS):
        lr.save_store(root, league, {"league": league, "matches": {}, "upcoming": [{"fixture_id": "fd-1", "home_id": "ITA_juventus", "away_id": "SCO_celtic_fc", "kickoff_utc": "2026-11-01T17:00:00+00:00"}]})
    stored = lr.load_store(root, "SUPERLIG")
    stored["matches"]["ITA_juventus|SCO_celtic_fc|2026-10-20"] = {"home_id": "ITA_juventus", "away_id": "SCO_celtic_fc", "kickoff_utc": "2026-10-20T17:00:00+00:00", "home_goals": 3, "away_goals": 3, "first_seen": "x"}
    lr.save_store(root, "SUPERLIG", stored)
    slugs = []

    def scanner(slug, start, end):
        slugs.append(slug)
        return [ef.parse_event(event(1, "Juventus", "Celtic", datetime(2026, 10, 20, 17, tzinfo=UTC), "STATUS_FULL_TIME", True, "1", "0")),       # differs from the stored 3-3
                ef.parse_event(event(2, "Ajax Amsterdam", "Celtic", datetime(2026, 10, 18, 17, tzinfo=UTC), "STATUS_FULL_TIME", True, "2", "2")),
                ef.parse_event(event(3, "Ajax Amsterdam", "Juventus", NOW + timedelta(days=2))),
                ef.parse_event(event(5, "Juventus", "Ajax Amsterdam", datetime(2026, 10, 10, 12, tzinfo=UTC), "STATUS_ABANDONED"))], []  # fmt: skip

    lines = lr.settle_from_espn(root, NOW, Dir(), scanner=scanner)
    assert len(lines) == 9 and len(slugs) == 9 and "eng.1" in slugs and "tur.1" in slugs and "uefa.champions" in slugs
    tr = lr.load_store(root, "SUPERLIG")
    assert tr["matches"]["ITA_juventus|SCO_celtic_fc|2026-10-20"]["home_goals"] == 3                         # the stored result is never changed
    assert tr["matches"]["NED_afc_ajax|SCO_celtic_fc|2026-10-18"]["source"].startswith("espn") and tr["upcoming"][0]["fixture_id"] == "fd-1" and len(tr["upcoming"]) == 1
    assert lr.load_store(root, "EPL")["matches"]["ITA_juventus|SCO_celtic_fc|2026-10-20"]["home_goals"] == 1
    assert tr["void"]["ITA_juventus|NED_afc_ajax|2026-10-10"]["status"] == "STATUS_ABANDONED" and "ITA_juventus|NED_afc_ajax|2026-10-10" not in tr["matches"]   # recorded with its status, never scored
    again = lr.settle_from_espn(root, NOW, Dir(), scanner=scanner)
    assert all("0 new result(s), 0 new void" in x for x in again)
    spring = [ef.parse_event(event(4, "Juventus", "Celtic", datetime(2027, 3, 14, 15, tzinfo=UTC), "STATUS_FULL_TIME", True, "2", "0"))]
    res, _, _ = lr.from_espn("SERIEA", spring, Dir(), datetime(2027, 3, 20, tzinfo=UTC), full_time_in_knockout=True)
    assert len(res) == 1
