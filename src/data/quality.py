"""Data quality report: row counts, null rates, duplicates, invalid values, unmatched teams.

Report has no wall-clock fields so identical inputs give an identical report.
"""

import json
from collections import Counter
from pathlib import Path

from .leagues import LEAGUES

MIN_SEASONS, MIN_LEAGUES = 5, 2  # S1 Definition of Done


def _null_rates(con, table: str) -> dict[str, float]:
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if n == 0:
        return {}
    exprs = ", ".join(f"avg(CASE WHEN {c} IS NULL THEN 1.0 ELSE 0.0 END)" for c in cols)
    vals = con.execute(f"SELECT {exprs} FROM {table}").fetchone()
    return {c: round(v, 4) for c, v in zip(cols, vals, strict=True)}


def build_report(con, data_version, manifest, raw_rows, rejected, cleaned, teams) -> dict:
    reasons = Counter(r["reason"] for r in rejected)
    invalid = Counter()
    for _, r in cleaned:
        invalid.update(r.invalid)
    coverage: dict[str, dict[str, dict]] = {}
    for league_id, season in sorted(raw_rows):
        n = con.execute(
            "SELECT count(*) FROM fixtures WHERE league_id=? AND season=?", [league_id, season]
        ).fetchone()[0]
        exp = LEAGUES[league_id].matches_per_season
        coverage.setdefault(league_id, {})[season] = {
            "raw_rows": raw_rows[(league_id, season)],
            "fixtures": n,
            "expected": exp,
            "complete": n == exp,
        }
    seasons = {s for lg in coverage.values() for s in lg}
    leagues = set(coverage)
    n_odds = con.execute("SELECT count(DISTINCT fixture_id) FROM odds_snapshots").fetchone()[0]
    n_fix = con.execute("SELECT count(*) FROM fixtures").fetchone()[0]
    unknown_time = con.execute(
        "SELECT count(*) FROM fixtures WHERE NOT kickoff_time_known"
    ).fetchone()[0]
    # every league needs >= MIN_SEASONS complete seasons
    full = {lg: sum(c["complete"] for c in ss.values()) for lg, ss in coverage.items()}
    dod_ok = len(leagues) >= MIN_LEAGUES and all(n >= MIN_SEASONS for n in full.values())
    return {
        "dataset_version": data_version,
        "summary": {
            "raw_rows": sum(raw_rows.values()),
            "accepted_fixtures": n_fix,
            "rejected_rows": len(rejected),
            "duplicates": reasons.get("duplicate", 0),
            "unmatched_team_rows": reasons.get("unmatched_team", 0),
            "leagues": sorted(leagues),
            "seasons": sorted(seasons),
            "meets_dod_5_seasons_2_leagues": dod_ok,
        },
        "rejected_by_reason": dict(sorted(reasons.items())),
        "invalid_values_nulled": dict(sorted(invalid.items())),
        "unmatched_teams": sorted(teams.review_queue.values(), key=lambda e: e["raw_name"]),
        "coverage": coverage,
        "null_rates": {
            t: _null_rates(con, t) for t in ("fixtures", "team_match_stats", "odds_snapshots")
        },
        "raw_files": [
            {"path": e.path, "league": e.league_id, "season": e.season, "sha256": e.checksum_sha256}
            for e in manifest
        ],
        "known_issues": [
            "Odds carry no per-snapshot timestamp (football-data): kind is pre_match_unspecified "
            "or closing; do not use pre_match_unspecified odds as time-accurate signals.",
            f"{unknown_time}/{n_fix} fixtures have no kickoff time; kickoff_utc set to 00:00 UTC "
            "of the match day (leakage-conservative).",
            f"{n_odds}/{n_fix} fixtures have odds.",
            "No xG in this source; xG features unavailable until another source is added.",
        ],
    }


def write_report(report: dict, reports_dir: Path, data_version: str) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"data_quality_{data_version}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    s = report["summary"]
    lines = [
        f"# Data quality report — {data_version}",
        "",
        f"- raw rows: {s['raw_rows']} | accepted: {s['accepted_fixtures']} | "
        f"rejected: {s['rejected_rows']} | duplicates: {s['duplicates']}",
        "- leagues: "
        + (", ".join(s["leagues"]) or "-")
        + " | seasons: "
        + (", ".join(s["seasons"]) or "-"),
        "- DoD (>=5 seasons, >=2 leagues): "
        + ("PASS" if s["meets_dod_5_seasons_2_leagues"] else "FAIL"),
        "",
        "## Rejected by reason",
        *[f"- {k}: {v}" for k, v in report["rejected_by_reason"].items()],
        "",
        "## Invalid values (nulled, not zeroed)",
        *[f"- {k}: {v}" for k, v in report["invalid_values_nulled"].items()],
        "",
        "## Unmatched teams (review queue)",
        *[
            f"- {t['raw_name']} ({t['country']}) -> suggestion: {t['suggestion']}"
            for t in report["unmatched_teams"]
        ],
        "",
        "## Coverage (fixtures / expected)",
    ]
    for lg, seasons in report["coverage"].items():
        for season, c in seasons.items():
            flag = "" if c["complete"] else "  <-- incomplete"
            lines.append(f"- {lg} {season}: {c['fixtures']}/{c['expected']}{flag}")
    lines += ["", "## Known issues", *[f"- {k}" for k in report["known_issues"]]]
    (reports_dir / f"data_quality_{data_version}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
