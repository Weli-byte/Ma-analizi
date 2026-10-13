"""European cups (ADR 0055): parser on real openfootball excerpts, cup-club registration, country-agnostic lookup."""

from datetime import UTC, date, datetime
from pathlib import Path

from src.data.teams import Alias, TeamDirectory
from src.ingestion.openfootball_cups import parse, score_90
from src.markets.cup_clubs import SOURCE, plan, slugify

FIX = Path(__file__).parent / "fixtures" / "real_provider_captures" / "openfootball_cups"


def test_score_90_variants_from_real_files():
    assert score_90("  2-2 (1-0)") == (2, 2)
    assert score_90("  0-0") == (0, 0)
    assert score_90("  3-2 a.e.t. (3-0, 1-0)") == (3, 0)  # the 90-minute score, not the extra-time one
    assert score_90("  4-3 pen. 1-1 a.e.t. (1-1, 0-1)") == (1, 1)
    assert score_90(None) is None
    assert score_90("   ") is None


def test_parse_champions_league_excerpt():
    ms = parse((FIX / "cl_2025-26_excerpt.txt").read_text(encoding="utf-8"), "UCL", 2025)
    assert ms[0].home == "Athletic Club" and ms[0].home_cc == "ESP" and ms[0].goals == (0, 2)
    assert ms[0].kickoff_utc == datetime(2025, 9, 16, 16, 45, tzinfo=UTC)  # 18:45 CEST
    assert ms[0].competition == "UCL" and ms[0].season == "2025-26"
    final = ms[-1]
    assert final.kickoff_utc.date() == date(2026, 5, 30)  # January-June belongs to the second calendar year
    assert final.goals == (1, 1) and final.stage.startswith("Finals")
    # a match without its own time line inherits the day, and the date line without a year keeps the year
    assert any(m.home == "PAE Olympiakos SFP" and m.goals == (0, 0) for m in ms)


def test_parse_conference_league_excerpt():
    ms = parse((FIX / "conf_2024-25_excerpt.txt").read_text(encoding="utf-8"), "UECL", 2024)
    assert ms and all(m.competition == "UECL" for m in ms)
    assert all(date(2024, 7, 1) <= m.kickoff_utc.date() <= date(2025, 6, 30) for m in ms)


def _directory() -> TeamDirectory:
    d = TeamDirectory({}, [], {}, {})
    d.teams["ENG_arsenal"] = {"team_id": "ENG_arsenal", "canonical_name": "Arsenal", "country": "ENG"}
    d.aliases.append(Alias("openfootball", "Arsenal FC", "ENG_arsenal"))
    return d


def test_cup_clubs_link_known_and_create_foreign():
    d = _directory()
    linked, created = plan(d, {("Arsenal FC", "ENG"): 5, ("SK Slavia Praha", "CZE"): 5})
    assert linked == [("Arsenal FC", "ENG", "ENG_arsenal")]
    assert created == [("SK Slavia Praha", "CZE", "CZE_sk_slavia_praha")]
    assert slugify("Qarabağ Ağdam FK") == "qarabag_agdam_fk"


def test_resolve_any_country_exact_loose_and_ambiguous():
    d = _directory()
    d.teams["CZE_slavia_praha"] = {
        "team_id": "CZE_slavia_praha",
        "canonical_name": "SK Slavia Praha",
        "country": "CZE",
    }
    d.aliases.append(Alias(SOURCE, "SK Slavia Praha", "CZE_slavia_praha"))
    srcs = ("the-odds-api", SOURCE, "openfootball")
    assert d.resolve_any_country(srcs, "Arsenal FC").team_id == "ENG_arsenal"
    assert d.resolve_any_country(srcs, "Slavia Praha").team_id == "CZE_slavia_praha"  # loose, unique
    assert d.resolve_any_country(srcs, "Nobody United").team_id is None
    # the same spelling for two clubs is never guessed
    d.teams["SCO_arsenal"] = {"team_id": "SCO_arsenal", "canonical_name": "Arsenal FC", "country": "SCO"}
    assert d.resolve_any_country(srcs, "Arsenal FC").team_id is None


def test_cup_history_loads_from_real_excerpt_and_uses_cache_on_network_failure(tmp_path, monkeypatch):
    from src.config import load_config
    from src.markets import of_source

    raw = (FIX / "cl_2025-26_excerpt.txt").read_bytes()
    d = TeamDirectory({}, [], {}, {})
    for text in parse(raw.decode("utf-8"), "UCL", 2025):
        for name, cc in ((text.home, text.home_cc), (text.away, text.away_cc)):
            tid = f"{cc}_{slugify(name)}"
            d.teams[tid] = {"team_id": tid, "canonical_name": name, "country": cc}
            d.aliases.append(Alias(SOURCE, name, tid))
    cfg = load_config("markets")
    now = datetime(2026, 10, 13, tzinfo=UTC)
    monkeypatch.setattr("src.ingestion.openfootball_cups.fetch", lambda comp, year: raw if comp == "UCL" and year == 2025 else (_ for _ in ()).throw(OSError("off")))  # fmt: skip
    blobs, stale = [], []
    hist, unresolved = of_source.cup_matches(tmp_path, d, cfg, 2025, now, blobs, stale)
    assert unresolved == 0 and len(hist) >= 10 and {m.league for m in hist} == {"UCL"}
    assert (
        hist[0].home_goals == 0
        and hist[0].away_goals == 2
        and hist[0].fixture_id.startswith("cup-UCL-2025-26-")
    )
    assert len(blobs) == 1 and stale == []
    # network down: the cache written above is used and reported as stale
    monkeypatch.setattr("src.ingestion.openfootball_cups.fetch", lambda comp, year: (_ for _ in ()).throw(OSError("off")))  # fmt: skip
    blobs2, stale2 = [], []
    hist2, _ = of_source.cup_matches(tmp_path, d, cfg, 2025, now, blobs2, stale2)
    assert len(hist2) == len(hist) and stale2 == ["UCL 2025"]
    # an unknown club is counted, never registered
    hist3, unresolved3 = of_source.cup_matches(
        tmp_path, TeamDirectory({}, [], {}, {}), cfg, 2025, now, [], []
    )
    assert hist3 == [] and unresolved3 > 0


def test_cup_clubs_cli_dry_run_and_apply(tmp_path, monkeypatch):
    import shutil

    from src.markets import cup_clubs

    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs")
    monkeypatch.setattr(
        cup_clubs, "cup_names", lambda: {("Arsenal FC", "ENG"): 3, ("Zzz Testklubben", "NOR"): 3}
    )
    assert cup_clubs.main(["--root", str(tmp_path)]) == 0
    assert cup_clubs.main(["--root", str(tmp_path), "--apply"]) == 0
    d = TeamDirectory.load(tmp_path / "configs" / "team_aliases.yaml")
    assert d.resolve(SOURCE, "Zzz Testklubben", "NOR").team_id == "NOR_zzz_testklubben"
    assert cup_clubs.main(["--root", str(tmp_path), "--apply"]) == 0  # idempotent
    assert len([a for a in d.aliases if a.source == SOURCE and a.raw_name == "Zzz Testklubben"]) == 1


def test_cup_events_resolve_without_a_country_in_the_odds_collection(tmp_path, monkeypatch):
    import shutil

    from src.odds import run as odds_run

    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs")
    d = TeamDirectory.load(tmp_path / "configs" / "team_aliases.yaml")
    for tid, name, cc in (("POR_a", "Alpha FC", "POR"), ("NED_b", "Beta SC", "NED")):
        d.teams[tid] = {"team_id": tid, "canonical_name": name, "country": cc}
    d.dump(tmp_path / "configs" / "team_aliases.yaml")
    kick = datetime(2026, 11, 3, 20, tzinfo=UTC)

    class Feed:
        def fetch(self, league, now):
            ev = {"status": "STATUS_SCHEDULED", "kickoff_utc": kick, "fixture_id": "oddsapi-x", "quotes": []}
            return [
                {**ev, "home_name": "Alpha", "away_name": "Beta"},
                {**ev, "fixture_id": "oddsapi-y", "home_name": "Alpha", "away_name": "Nobody"},
            ]

    lines = odds_run._collect_feed(tmp_path, "UCL", Feed(), "the-odds-api", datetime(2026, 11, 1, tzinfo=UTC))
    assert any("oddsapi-x" in x and "SKIPPED" not in x for x in lines)
    assert any("oddsapi-y" in x and "SKIPPED unresolved" in x for x in lines)
