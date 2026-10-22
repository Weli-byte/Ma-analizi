"""openfootball Football.TXT parser (CC0 source) and the shadow history top-ups (ADR 0069 data note).

The text samples below are excerpts copied from the real files `openfootball/europe/turkey/2026-27_tr1.txt` and `openfootball/england/2026-27/1-premierleague.txt`
(read 2026-10-22). Fake clubs / providers in the overlay tests are labelled TEST_ONLY."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.evaluation.dataset import EvalRow
from src.ingestion.openfootball_txt import fetch_to_cache, parse_txt, url_for
from src.ingestion.provider import RawFixture
from src.league_models.rollout import shadow

NEW_LAYOUT = """= Turkey | Süper Lig 2026/27

# Dates    Fri Aug 14 2026 - Sun May 23 2027 (282d)

▪ Regular Season - 1
Fri Aug 14 2026
  21:30   Galatasaray  2-2 (0-0)  Çorum
                  (Victor OSIMHEN 53', 90';
                   Alexandros Kyziridis 59', Jesus RAMIREZ 61')
Sat Aug 15
  19:00   Atiker Konyaspor  0-1 (0-0)  Çaykur Rizespor
                  (Qazim LACI 90+7'(p))
          Kasimpasa  1-1 (0-1)  Trabzonspor
  21:30   Gaziantep FK  1-1 (0-1)  Aytemiz Alanyaspor

▪ Regular Season - 9
Sun Oct 25
          Gaziantep FK   v   BB Erzurumspor
Mon Jan 4
          Göztepe   v   Çorum
"""

OLD_LAYOUT = """= English Premier League 2026/27

▪ Matchday 1
  Fri Aug 21 2026
    20:00  Arsenal FC              v Coventry City FC         3-0 (2-0)
  Sat Aug 22
    12:30  Hull City AFC           v Manchester United FC     2-0 (2-0)
           Ipswich Town FC         v Sunderland AFC
"""


def test_the_new_layout_is_read_with_times_inherited_scores_and_the_year_rollover():
    doc, rep = parse_txt(NEW_LAYOUT)
    m = doc["matches"]
    assert rep == {"matches": 6, "scored": 4, "skipped_lines": 0}
    assert m[0] == {"round": "Regular Season - 1", "date": "2026-08-14", "team1": "Galatasaray", "team2": "Çorum", "time": "21:30", "score": {"ft": [2, 2]}}
    assert m[1]["time"] == "19:00" and m[1]["score"]["ft"] == [0, 1]
    assert m[2]["time"] == "19:00" and m[2]["team1"] == "Kasimpasa"            # no time on the line: the previous time of that day applies
    assert m[3]["time"] == "21:30" and m[3]["score"]["ft"] == [1, 1]
    assert m[4]["date"] == "2026-10-25" and "score" not in m[4] and "time" not in m[4]   # a scheduled match has no score and, here, no time
    assert m[5]["date"] == "2027-01-04"                                         # October -> January rolls the year forward


def test_the_older_layout_with_the_result_after_the_away_team_is_read_too():
    doc, rep = parse_txt(OLD_LAYOUT)
    assert rep["matches"] == 3 and rep["scored"] == 2 and rep["skipped_lines"] == 0
    assert doc["matches"][1]["team1"] == "Hull City AFC" and doc["matches"][1]["team2"] == "Manchester United FC" and doc["matches"][1]["score"]["ft"] == [2, 0]
    assert "score" not in doc["matches"][2] and doc["matches"][2]["time"] == "12:30"


def test_a_line_that_is_not_a_date_round_or_match_is_counted_not_guessed():
    doc, rep = parse_txt("▪ Round 1\nFri Aug 14 2026\n  nonsense without a separator\n  21:30  A  1-0  B\n")
    assert rep["matches"] == 1 and rep["skipped_lines"] == 1
    doc2, rep2 = parse_txt("Sat Aug 15\n  19:00  A  1-0  B\n")                    # a date without a year before any full date cannot be placed
    assert rep2["matches"] == 0 and rep2["skipped_lines"] >= 1


def test_the_urls_and_the_cache_location_never_touch_the_frozen_research_snapshot(tmp_path, monkeypatch):
    assert url_for("TUR1", 2026) == "https://raw.githubusercontent.com/openfootball/europe/master/turkey/2026-27_tr1.txt"
    monkeypatch.setattr("src.ingestion.openfootball_txt.logged_urlopen", lambda *a, **k: NEW_LAYOUT.encode("utf-8"))
    out = fetch_to_cache(tmp_path, "TUR1", 2026)
    path = tmp_path / out["path"]
    assert "operational_cache" in out["path"] and "markets/_cache" not in out["path"].replace("\\", "/") and out["scored"] == 4
    assert json.loads(path.read_text(encoding="utf-8"))["matches"][0]["team1"] == "Galatasaray" and len(out["sha256_of_source"]) == 64


# ------------------------------------------------------------------ history top-ups for the shadow run (TEST_ONLY clubs)
class Dir:
    def resolve(self, source, name, country, day=None, **kw):
        class R:
            team_id = None

        r = R()
        if name.startswith("Club "):
            r.team_id = f"{country}_club_{name.split()[1].lower()}"
        return r


def _hist(home="ITA_club_a", away="ITA_club_b", when=datetime(2026, 9, 12, 15, tzinfo=UTC)):
    return [EvalRow("h1", "ITA1", "2026-27", when, home, away, 0, home_goals=2, away_goals=0)]


def _fd(i, home, away, when, hg=1, ag=1, status="FT"):
    return RawFixture(str(i), "SA", "2026-27", when, home, away, status, hg, ag)


def test_the_research_overlay_adds_only_missing_finished_results_and_never_a_future_or_unresolved_one():
    now = datetime(2026, 10, 22, 18, tzinfo=UTC)
    fin = [
        ("ITA1", _fd(1, "Club A", "Club B", datetime(2026, 9, 12, 19, tzinfo=UTC), 2, 0)),      # already in the licence-clean history (same day +- 1): skipped
        ("ITA1", _fd(2, "Club A", "Club B", datetime(2026, 10, 3, 15, tzinfo=UTC), 3, 1)),      # new, finished: added
        ("ITA1", _fd(3, "Club C", "Club A", datetime(2026, 10, 21, 19, tzinfo=UTC), 0, 0)),      # finished 23 h ago: past the 3 h result lag, added
        ("ITA1", _fd(4, "Club C", "Club B", now - timedelta(hours=1), 1, 0)),                   # inside the result lag: not history yet
        ("ITA1", _fd(5, "Mystery FC", "Club B", datetime(2026, 10, 4, 15, tzinfo=UTC), 1, 0)),  # unresolved club: skipped and counted
        ("ITA1", RawFixture("6", "SA", "2026-27", datetime(2026, 10, 5, 15, tzinfo=UTC), "Club A", "Club C", "FT", None, None)),   # no score: not a result
    ]
    rows, rep = shadow.research_overlay_rows(fin, Dir(), _hist(), now, 3, {"ITA1": "ITA"})
    assert sorted(r.fixture_id for r in rows) == ["fdorg-res-2", "fdorg-res-3"] and rep["rows_added"] == 2 and rep["unresolved_clubs_skipped"] == 1
    r2 = next(r for r in rows if r.fixture_id == "fdorg-res-2")
    assert (r2.home_goals, r2.away_goals, r2.outcome) == (3, 1, 0) and r2.league_id == "ITA1" and r2.kickoff_utc.tzinfo is not None


def test_operational_extra_rows_come_from_the_public_domain_cache_and_skip_what_is_known(tmp_path):
    base = tmp_path / "artifacts" / "league_models" / "operational_cache" / "openfootball-europe-txt" / "2026-27"
    base.mkdir(parents=True)
    doc, _ = parse_txt(NEW_LAYOUT)
    (base / "tr.1.json").write_text(json.dumps(doc), encoding="utf-8")

    class TurDir:
        def resolve(self, source, name, country, day=None, **kw):
            class R:
                team_id = None

            r = R()
            r.team_id = f"TUR_{name.lower().replace(' ', '_')}" if name != "Çorum" else None   # one club the alias table does not know
            return r

    now = datetime(2026, 10, 22, 18, tzinfo=UTC)
    rows, rep = shadow.operational_extra_rows(tmp_path, TurDir(), [], now, 3)
    assert rep["files"] and rep["rows_added"] == 3                      # 4 scored matches, one involves the unknown club
    assert all(r.league_id == "TUR1" and r.home_goals is not None for r in rows)
    again, _ = shadow.operational_extra_rows(tmp_path, TurDir(), rows, now, 3)
    assert again == []                                                  # already in the history: nothing is added twice
    none, rep2 = shadow.operational_extra_rows(tmp_path / "empty", TurDir(), [], now, 3)
    assert none == [] and rep2["files"] == []


def test_the_overlay_is_a_documented_default_and_flagged_research_only():
    cfg = shadow.load_rollout()
    assert cfg["shadow"]["research_overlay"] is True and cfg["kill_switch"] is True
    assert "RESEARCH ONLY" in Path(__file__).parents[1].joinpath("configs/league_models/rollout.yaml").read_text(encoding="utf-8")
