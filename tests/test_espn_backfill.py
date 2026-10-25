"""Historical backfill of the Europa / Conference League results from ESPN (ADR 0074). TEST_ONLY synthetic scoreboard events with the real field layout."""

import json
from datetime import UTC, date, datetime

from src.markets import espn_backfill
from src.markets import live_results as lr
from src.markets.of_source import live_matches
from tests.test_espn_fixtures import Dir, event

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def ev(i, home, away, when, status, completed=True, hs="1", as_="0"):
    return event(i, home, away, when, status, completed, hs, as_)


def scanner_for(events):
    from src.ingestion import espn_fixtures as ef

    parsed = [ef.parse_event(e) for e in events]

    def scan(slug, start, end):
        return parsed, []

    return scan


def events():
    return [
        ev(1, "Juventus", "Stade Rennais", datetime(2025, 10, 23, 16, 45, tzinfo=UTC), "STATUS_FULL_TIME", True, "2", "1"),               # league phase
        ev(2, "Juventus", "Celtic", datetime(2026, 3, 12, 17, 45, tzinfo=UTC), "STATUS_FULL_TIME", True, "1", "1"),                       # knockout month, decided in 90 minutes
        ev(3, "Ajax Amsterdam", "Celtic", datetime(2026, 3, 19, 17, 45, tzinfo=UTC), "STATUS_FINAL_AET", True, "3", "2"),                 # extra time: the 90-minute score is unknown
        ev(4, "Ajax Amsterdam", "Juventus", datetime(2026, 4, 2, 17, 45, tzinfo=UTC), "STATUS_FINAL_PEN", True, "1", "1"),                # penalties
        ev(5, "Mystery FC", "Celtic", datetime(2025, 11, 6, 17, 45, tzinfo=UTC), "STATUS_FULL_TIME", True, "0", "0"),                     # unresolved club
    ]


def test_the_backfill_adds_90_minute_results_only_and_reports_what_it_left_out(tmp_path):
    rep = espn_backfill.backfill(tmp_path, Dir(), date(2025, 7, 1), date(2026, 7, 1), NOW, scanner=scanner_for(events()), leagues=("UEL",))[0]
    assert rep["results_added"] == 2 and rep["unresolved_clubs"] == ["Mystery FC"] and rep["knockout_matches_not_added_extra_time_or_penalties"] == 2 and rep["status"] == "OK"
    store = json.loads(lr.results_path(tmp_path, "UEL").read_text(encoding="utf-8"))
    rows = sorted(store["matches"].values(), key=lambda r: r["kickoff_utc"])
    assert [(r["home_goals"], r["away_goals"], r["season"]) for r in rows] == [(2, 1, "2025-26"), (1, 1, "2025-26")]
    assert all(r["first_seen"] == NOW.isoformat() for r in rows)                      # availability is the day we first saw it, never earlier


def test_the_marker_makes_the_cloud_call_a_no_op_after_the_first_complete_scan(tmp_path):
    calls = []

    def scan(slug, start, end):
        calls.append(slug)
        return scanner_for(events())(slug, start, end)

    args = (tmp_path, Dir(), date(2025, 7, 1), date(2026, 7, 1), NOW)
    first = espn_backfill.backfill(*args, if_needed=True, scanner=scan, leagues=("UEL",))
    again = espn_backfill.backfill(*args, if_needed=True, scanner=scan, leagues=("UEL",))
    assert first[0]["status"] == "OK" and again[0]["status"] == "ALREADY_DONE" and len(calls) == 1
    other = espn_backfill.backfill(tmp_path, Dir(), date(2024, 7, 1), date(2025, 7, 1), NOW, if_needed=True, scanner=scan, leagues=("UEL",))   # another window is scanned
    assert other[0]["status"] == "OK" and len(calls) == 2


def test_a_failed_day_is_not_marked_done_so_the_next_run_repeats_the_window(tmp_path):
    def scan(slug, start, end):
        return [], ["espn uefa.europa 2025-10-23: URLError"]

    rep = espn_backfill.backfill(tmp_path, Dir(), date(2025, 7, 1), date(2026, 7, 1), NOW, if_needed=True, scanner=scan, leagues=("UEL",))[0]
    assert "DAYS FAILED" in rep["status"] and "espn_backfill_done" not in lr.load_store(tmp_path, "UEL")


def test_the_live_collection_still_refuses_knockout_scores_and_a_backfilled_row_keeps_its_own_season(tmp_path):
    from src.ingestion import espn_fixtures as ef

    ms = [ef.parse_event(events()[1])]
    assert lr.from_espn("UEL", ms, Dir(), NOW)[0] == []                                # live collection: knockout months stay refused
    assert len(lr.from_espn("UEL", ms, Dir(), NOW, full_time_in_knockout=True)[0]) == 1
    espn_backfill.backfill(tmp_path, Dir(), date(2025, 7, 1), date(2026, 7, 1), NOW, scanner=scanner_for(events()), leagues=("UEL",))
    store = lr.load_store(tmp_path, "UEL")
    store["season"] = "2026-27"                                                       # the store-level label is the current season
    lr.save_store(tmp_path, "UEL", store)

    class Cfg:
        result_lag_hours = 3

    out = live_matches(tmp_path, set(), Cfg(), NOW, [])
    assert {m.season for m in out} == {"2025-26"} and len(out) == 2                    # not mislabelled with the current season


def test_a_new_espn_alias_makes_the_marked_window_scan_once_more(tmp_path):
    from types import SimpleNamespace

    calls = []

    def scan(slug, start, end):
        calls.append(slug)
        return scanner_for(events())(slug, start, end)

    class D(Dir):
        aliases = [SimpleNamespace(source="espn", raw_name="Juventus")]

    args = (tmp_path, D(), date(2025, 7, 1), date(2026, 7, 1), NOW)
    assert espn_backfill.backfill(*args, if_needed=True, scanner=scan, leagues=("UEL",))[0]["status"] == "OK"
    assert espn_backfill.backfill(*args, if_needed=True, scanner=scan, leagues=("UEL",))[0]["status"] == "ALREADY_DONE" and len(calls) == 1

    class D2(Dir):
        aliases = [SimpleNamespace(source="espn", raw_name="Juventus"), SimpleNamespace(source="espn", raw_name="Breidablik")]       # a club that did not resolve before is now known

    again = espn_backfill.backfill(tmp_path, D2(), date(2025, 7, 1), date(2026, 7, 1), NOW, if_needed=True, scanner=scan, leagues=("UEL",))
    assert again[0]["status"] == "OK" and len(calls) == 2                                           # scanned once more; the merge is idempotent, nothing is added twice
