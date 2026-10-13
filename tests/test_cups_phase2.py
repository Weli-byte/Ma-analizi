"""European cups, phase 2 (ADR 0056): priors, neutral venue, domestic history, duplicate clubs, free events check."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.config import load_config
from src.data.teams import Alias, TeamDirectory
from src.markets import club_duplicates, eval_priors
from src.markets.coefficients import coefficients, uefa_priors
from src.markets.cup_clubs import plan_domestic
from src.markets.data import STATS, StatMatch
from src.markets.model import fit_rates

ROOT = Path(__file__).resolve().parents[1]
EMPTY = dict.fromkeys(STATS)


def sm(i, season, league, ko, h, a, hg, ag, neutral=False):
    return StatMatch(
        f"m{i}", season, league, ko, ko + timedelta(hours=3), h, a, hg, ag, EMPTY, EMPTY, None, neutral
    )


def synthetic(seed=1):
    """Three countries, four clubs each; domestic league EPL (shared mu) and UCL cup matches over three seasons."""
    rng = np.random.default_rng(seed)
    teams = [f"{cc}_t{i}" for cc in ("ENG", "ESP", "POR") for i in range(4)]
    strength = {t: 1.4 if t.endswith(("0", "1")) else 0.8 for t in teams}
    out, k = [], 0
    for yr in (2022, 2023, 2024):
        season = f"{yr}-{str(yr + 1)[2:]}"
        for rnd in range(60):
            h, a = rng.choice(teams, 2, replace=False)
            ko = datetime(yr, 8, 1, tzinfo=UTC) + timedelta(days=rnd * 5)
            league = "UCL" if rnd % 3 == 0 else "EPL"
            hg, ag = int(rng.poisson(1.4 * strength[h] / strength[a] ** 0.5)), int(rng.poisson(1.1 * strength[a] / strength[h] ** 0.5))  # fmt: skip
            out.append(sm(k, season, league, ko, h, a, hg, ag))
            k += 1
    return out


def test_coefficients_use_only_results_available_at_the_cutoff():
    ms = synthetic()
    cut = datetime(2024, 1, 1, tzinfo=UTC)
    club, country = coefficients(ms, cut)
    assert club and set(country) <= {"ENG", "ESP", "POR"}
    later = [m for m in ms if m.available_at > cut]
    assert later  # the synthetic data has future rows
    club2, _ = coefficients([m for m in ms if m.available_at <= cut], cut)
    assert club == club2  # rows after the cutoff change nothing
    p = uefa_priors(ms, datetime(2025, 8, 1, tzinfo=UTC), 0.2, 0.8)
    assert set(p) == {m.home_id for m in ms} | {m.away_id for m in ms}
    assert uefa_priors(ms, cut, 0.0, 0.0) == {}
    assert uefa_priors(ms[:3], cut, 0.2, 0.8) == {}  # too little cup data: no prior


def test_prior_pulls_a_team_without_data_and_neutral_uses_the_midpoint():
    cfg = load_config("markets")
    ms = synthetic()
    h, a = [m.home_id for m in ms], [m.away_id for m in ms]
    lg = [m.league for m in ms]
    hy = np.array([m.home_goals for m in ms], float)
    ay = np.array([m.away_goals for m in ms], float)
    w = np.ones(len(ms))
    base = fit_rates(hy, ay, h, a, lg, w, cfg)
    up = fit_rates(np.append(hy, 0.0), np.append(ay, 0.0), [*h, "ENG_new"], [*a, "ESP_t0"], [*lg, "UCL"], np.append(w, 1e-9), cfg,
                   prior={"ENG_new": 1.0})  # fmt: skip
    down = fit_rates(np.append(hy, 0.0), np.append(ay, 0.0), [*h, "ENG_new"], [*a, "ESP_t0"], [*lg, "UCL"], np.append(w, 1e-9), cfg,
                     prior={"ENG_new": -1.0})  # fmt: skip
    assert up.attack["ENG_new"] > base.attack.get("ENG_new", 1.0) > down.attack["ENG_new"]
    t = base.lambdas("ENG_t0", "ESP_t0", "UCL", neutral=True)
    mid = (base.mu_home["UCL"] + base.mu_away["UCL"]) / 2
    assert abs(t[0] - mid * base.attack["ENG_t0"] * base.defence["ESP_t0"]) < 1e-12
    neu = np.array([m.neutral for m in ms], bool)
    neu[::7] = True
    flagged = fit_rates(hy, ay, h, a, lg, w, cfg, neutral=neu)
    assert flagged.mu_home != base.mu_home  # neutral rows no longer feed the home level


def test_neutral_stage_and_final_date_rules():
    from src.ingestion.openfootball_cups import is_final_date, is_neutral_stage

    assert is_neutral_stage("UCL", "2025-26", "Finals, Final") and is_neutral_stage("UEL", "2022-23", "Final")
    assert not is_neutral_stage("UCL", "2025-26", "Finals, Semifinals")
    assert is_neutral_stage("UCL", "2019-20", "Quarterfinals") and not is_neutral_stage("UEL", "2020-21", "Quarterfinals")  # fmt: skip
    final = datetime(2026, 5, 30, 19, tzinfo=UTC)
    assert is_final_date("UCL", final, [final])
    semi = datetime(2026, 5, 6, 19, tzinfo=UTC)
    assert not is_final_date("UCL", semi, [semi, semi])  # April / early May: not a final
    pair_late = datetime(2023, 5, 18, 19, tzinfo=UTC)
    assert not is_final_date(
        "UEL", pair_late, [pair_late, pair_late + timedelta(hours=1)]
    )  # a semi-final pair
    assert not is_final_date("EPL", final, [final])


def test_domestic_plan_groups_spellings_and_links_known_clubs():
    d = TeamDirectory({}, [], {}, {})
    d.teams["POR_sporting_cp"] = {
        "team_id": "POR_sporting_cp",
        "canonical_name": "Sporting CP",
        "country": "POR",
    }
    d.aliases.append(Alias("openfootball-cups", "Sporting CP", "POR_sporting_cp"))
    names = {
        ("Sporting CP", "POR"): 9,
        ("Boavista", "POR"): 5,
        ("Boavista FC", "POR"): 5,
        ("Gil Vicente FC", "POR"): 4,
    }
    linked, created = plan_domestic(d, names)
    assert linked == []  # exact name known for the cups source: resolved under "openfootball"? not yet
    tids = {n: t for n, _, t in created}
    assert tids["Boavista"] == tids["Boavista FC"] == "POR_boavista"
    d.aliases.append(Alias("openfootball", "Sporting CP", "POR_sporting_cp"))
    linked2, _ = plan_domestic(d, {("SC Sporting CP", "POR"): 3})
    assert linked2 == [("SC Sporting CP", "POR", "POR_sporting_cp")]  # unique loose match in the same country


def test_duplicate_proposals_skip_clubs_that_met_and_merge_needs_same_country():
    d = TeamDirectory({}, [], {}, {})
    for tid, name, cc in (
        ("SCO_dundee", "Dundee FC", "SCO"),
        ("SCO_dundee_united", "Dundee United", "SCO"),
        ("POR_boavista", "Boavista", "POR"),
        ("POR_boavista_fc", "Boavista FC", "POR"),
        ("NED_az", "AZ", "NED"),
        ("NED_az_alkmaar", "AZ Alkmaar", "NED"),
        ("ENG_boavista", "Boavista", "ENG"),
    ):
        d.teams[tid] = {"team_id": tid, "canonical_name": name, "country": cc}
    d.aliases.append(Alias("openfootball", "AZ", "NED_az"))
    met = {frozenset(("SCO_dundee", "SCO_dundee_united"))}
    rows = club_duplicates.propose(d, met, {"POR_boavista": 10, "POR_boavista_fc": 4, "NED_az_alkmaar": 7})
    pairs = {(r["keep"], r["drop"]) for r in rows}
    assert ("POR_boavista", "POR_boavista_fc") in pairs and ("NED_az_alkmaar", "NED_az") in pairs
    assert not any("SCO_dundee" in (r["keep"], r["drop"]) for r in rows)
    assert "POR_boavista_fc" in club_duplicates.render(rows)
    assert club_duplicates.merge(d, "NED_az", "NED_az_alkmaar") == 1
    assert "NED_az" not in d.teams and d.aliases[0].team_id == "NED_az_alkmaar"
    for bad in (("ENG_boavista", "POR_boavista"), ("NOPE", "POR_boavista")):
        try:
            club_duplicates.merge(d, *bad)
        except (ValueError, KeyError):
            pass
        else:
            raise AssertionError("merge must refuse")


def test_duplicates_cli_apply(tmp_path):
    import shutil

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    path = tmp_path / "configs" / "team_aliases.yaml"
    d = TeamDirectory.load(path)
    for tid, name in (("ENG_zz_one", "Zz One"), ("ENG_zz_one_fc", "Zz One FC")):
        d.teams[tid] = {"team_id": tid, "canonical_name": name, "country": "ENG"}
    d.dump(path)
    assert club_duplicates.main(["--root", str(tmp_path), "--apply", "ENG_zz_one_fc=ENG_zz_one"]) == 0
    after = TeamDirectory.load(path)
    assert "ENG_zz_one_fc" not in after.teams and "ENG_zz_one" in after.teams


def test_events_check_reads_kickoffs_without_a_paid_call(monkeypatch):
    from src.odds import theoddsapi

    seen = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps([{"commence_time": "2026-11-03T20:00:00Z"}]).encode()

    monkeypatch.setattr(theoddsapi.urllib.request, "urlopen", lambda url, timeout: seen.append(url) or R())
    ks = theoddsapi.TheOddsApiFeed("secret").kickoffs("UCL")
    assert ks == [datetime(2026, 11, 3, 20, tzinfo=UTC)]
    assert "/events" in seen[0] and "/odds" not in seen[0]


def test_prior_evaluation_runs_on_validation_seasons_only(tmp_path, monkeypatch):
    class World:
        history = synthetic(3)

    monkeypatch.setattr(eval_priors, "load_world", lambda root, directory, cfg, end_year=None: World)
    monkeypatch.setattr(eval_priors, "COUNTRY_GRID", [0.0, 0.2])
    monkeypatch.setattr(eval_priors, "CLUB_GRID", [0.0, 0.8])
    monkeypatch.setattr(eval_priors, "origins", lambda seasons: [datetime(2024, 1, 1, tzinfo=UTC)])
    import shutil

    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    res = eval_priors.evaluate(tmp_path)
    assert len(res["grid"]) == 4 and res["validation_seasons"] == ["2022-23", "2023-24"]
    assert eval_priors.main(["--root", str(tmp_path)]) == 0
    assert (tmp_path / "artifacts" / "markets" / "_eval" / "uefa_priors.json").exists()
    o = eval_priors.__dict__["origins"]
    assert o(["2022-23"])  # monkeypatched; the real helper is covered below


def test_origins_cover_three_dates_per_season():
    import importlib

    real = importlib.reload(eval_priors).origins(["2022-23", "2023-24"])
    assert len(real) == 6 and real[0] == datetime(2022, 10, 1, tzinfo=UTC) and real[1].year == 2023
