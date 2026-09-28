"""Data quality engine: explicit checks, season classification, JSON + Markdown report.

Severity: `error` checks make RESEARCH/STRICT/FINAL runs fail; `warning` checks fail STRICT/FINAL
only (see src/runmode.py). Report content is deterministic (no wall-clock fields).
"""

import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.config import DataConfig, LeaguesConfig
from src.schemas import FixtureStatus, SeasonStatus

from .clean import CleanRow
from .leagues import season_date_window
from .manifest import Manifest
from .teams import TeamDirectory


@dataclass(frozen=True)
class CheckResult:
    id: str
    name: str
    severity: str  # error | warning
    passed: bool
    observed: object
    detail: str = ""


@dataclass(frozen=True)
class SeasonInfo:
    league: str
    season: str
    status: SeasonStatus
    n_fixtures: int
    n_finished: int
    expected: int
    first_date: str | None
    last_date: str | None


@dataclass
class QualityInputs:
    manifest: Manifest
    cleaned: list[tuple[str, CleanRow]]  # (source filename, row)
    rejected: list[dict]  # {file, reason, detail, league_season}
    raw_rows: dict[tuple[str, str], int]  # (league, season) -> raw data rows
    raw_duplicate_rows: int
    teams: TeamDirectory
    leagues: LeaguesConfig
    data_cfg: DataConfig
    as_of: date
    file_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _end_of_month(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def classify_seasons(inp: QualityInputs) -> dict[tuple[str, str], SeasonInfo]:
    by_ls: dict[tuple[str, str], list[CleanRow]] = {}
    for _, r in inp.cleaned:
        by_ls.setdefault((r.fixture.league_id, r.fixture.season), []).append(r)
    out: dict[tuple[str, str], SeasonInfo] = {}
    for (lid, season), rows in sorted(by_ls.items()):
        fmt = inp.leagues.leagues[lid]
        expected = fmt.matches_per_season
        finished = [r for r in rows if r.fixture.status == FixtureStatus.FINISHED]
        scheduled = len(rows) - len(finished)
        _, (ey, em) = season_date_window(season, fmt)
        season_end = _end_of_month(ey, em)
        if len(finished) == expected and scheduled == 0:
            status = SeasonStatus.HISTORICAL_COMPLETE
        elif inp.as_of <= season_end:
            status = SeasonStatus.CURRENT_PARTIAL
        else:
            status = SeasonStatus.INCOMPLETE_HISTORICAL
        days = sorted(r.match_day for r in rows if r.match_day)
        out[(lid, season)] = SeasonInfo(
            lid,
            season,
            status,
            len(rows),
            len(finished),
            expected,
            days[0].isoformat() if days else None,
            days[-1].isoformat() if days else None,
        )
    return out


def _cap(items: list[str], n: int = 5) -> list[str]:
    return items[:n] + ([f"... {len(items)} total"] if len(items) > n else [])


def _split_ack(items: list[str], check_id: str, inp: QualityInputs) -> tuple[list[str], list[str]]:
    acks = [a for a in inp.data_cfg.acknowledged_anomalies if a.check == check_id]
    kept = [i for i in items if not any(a.match in i for a in acks)]
    return kept, [i for i in items if i not in kept]


def run_checks(inp: QualityInputs) -> tuple[list[CheckResult], dict[tuple[str, str], SeasonInfo]]:
    seasons = classify_seasons(inp)
    checks: list[CheckResult] = []
    rej = Counter(r["reason"] for r in inp.rejected)

    def add(cid: str, name: str, sev: str, passed: bool, observed: object, detail: str = "") -> None:
        checks.append(CheckResult(cid, name, sev, bool(passed), observed, detail))

    entries = inp.manifest.entries
    add(
        "Q01",
        "required_columns",
        "error",
        True,
        len(entries),
        "validated per file when the manifest is built (missing columns raise RawFileError)",
    )
    non_utf8 = [e.filename for e in entries if e.encoding != "utf-8"]
    add("Q02", "encoding_utf8", "warning", not non_utf8, non_utf8)
    dup = rej.get("duplicate", 0)
    add("Q03", "duplicate_fixtures", "error", dup == 0, dup)
    add("Q04", "duplicate_raw_rows", "warning", inp.raw_duplicate_rows == 0, inp.raw_duplicate_rows)
    bad_dates_rej = rej.get("invalid_date", 0) + rej.get("invalid_local_time", 0)
    add(
        "Q05",
        "valid_dates",
        "error",
        bad_dates_rej == 0,
        {k: rej.get(k, 0) for k in ("invalid_date", "invalid_local_time")},
    )
    bad_scores = rej.get("invalid_score", 0) + rej.get("missing_score", 0)
    add(
        "Q06",
        "score_ranges",
        "error",
        bad_scores == 0,
        {k: rej.get(k, 0) for k in ("invalid_score", "missing_score")},
    )
    add(
        "Q07",
        "ftr_consistency",
        "error",
        rej.get("result_mismatch", 0) == 0,
        rej.get("result_mismatch", 0),
    )

    odds_invalid = sum(
        r.invalid.get("odds_le_1", 0) + r.invalid.get("odds_incomplete", 0) for _, r in inp.cleaned
    )
    add(
        "Q08",
        "odds_greater_than_one",
        "warning",
        odds_invalid == 0,
        odds_invalid,
        "offending odds were nulled, never zero-filled",
    )

    finished = [r for _, r in inp.cleaned if r.fixture.status == FixtureStatus.FINISHED]
    n_f = max(len(finished), 1)
    null_rates = {}
    for stat in ("shots", "shots_on_target", "corners", "yellow_cards"):
        nulls = sum(1 for r in finished for s in r.stats if s.get(stat) is None)
        null_rates[stat] = round(nulls / (2 * n_f), 4)
    add(
        "Q09",
        "null_rates",
        "warning",
        all(v <= 0.2 for v in null_rates.values()),
        null_rates,
        "warning when a tracked stat is missing in more than 20% of team-matches",
    )

    unresolved = len(inp.teams.review_queue)
    add(
        "Q10",
        "team_resolution",
        "error",
        unresolved == 0 and rej.get("unmatched_team", 0) == 0,
        {"unresolved_names": unresolved, "rows": rej.get("unmatched_team", 0)},
        "see: python -m src.data.team_resolution review",
    )

    team_counts: dict[tuple[str, str], Counter] = {}
    for _, r in inp.cleaned:
        f = r.fixture
        if f.status == FixtureStatus.FINISHED:
            c = team_counts.setdefault((f.league_id, f.season), Counter())
            c[f.home_id] += 1
            c[f.away_id] += 1
    bad_teams: list[str] = []
    bad_pt: list[str] = []
    bad_ns: list[str] = []
    for key, info in seasons.items():
        fmt = inp.leagues.leagues[key[0]]
        counts = team_counts.get(key, Counter())
        if info.status == SeasonStatus.INCOMPLETE_HISTORICAL:
            bad_ns.append(f"{key[0]} {key[1]}: {info.n_finished}/{info.expected} (season is over)")
        elif info.n_finished > info.expected:
            bad_ns.append(f"{key[0]} {key[1]}: {info.n_finished} > {info.expected}")
        if info.status != SeasonStatus.CURRENT_PARTIAL:  # finished seasons must be structurally complete
            if len(counts) != fmt.n_teams:
                bad_teams.append(f"{key[0]} {key[1]}: {len(counts)} teams != {fmt.n_teams}")
            wrong = {t: n for t, n in counts.items() if n != fmt.matches_per_team}
            if wrong:
                bad_pt.append(f"{key[0]} {key[1]}: {dict(sorted(wrong.items()))}")
    add("Q11", "expected_team_count", "error", not bad_teams, bad_teams)
    add("Q12", "expected_matches_per_team", "error", not bad_pt, bad_pt)
    add("Q13", "expected_matches_per_season", "error", not bad_ns, bad_ns)

    bad_window: list[str] = []
    bad_future: list[str] = []
    for _, r in inp.cleaned:
        f = r.fixture
        d = r.match_day
        if d is None:
            continue
        (sy, sm), (ey, em) = season_date_window(f.season, inp.leagues.leagues[f.league_id])
        if not (date(sy, sm, 1) <= d <= _end_of_month(ey, em)):
            bad_window.append(f"{f.fixture_id}: {d} outside season window")
        if d.year < 1990 or (f.status == FixtureStatus.FINISHED and d > inp.as_of):
            bad_future.append(f"{f.fixture_id}: {d}")
    add("Q14", "season_date_boundaries", "error", not bad_window, _cap(bad_window))
    add("Q15", "impossible_dates", "error", not bad_future, _cap(bad_future))

    anomalies = []
    for _, r in inp.cleaned:
        if r.goals is not None:
            fmt = inp.leagues.leagues[r.fixture.league_id]
            if max(r.goals) > fmt.max_plausible_goals:
                anomalies.append(f"{r.fixture.fixture_id}: {r.goals}")
    anomalies, ack16 = _split_ack(anomalies, "Q16", inp)
    add(
        "Q16",
        "anomalous_scores",
        "warning",
        not anomalies,
        _cap(anomalies),
        f"acknowledged (reviewed) anomalies: {ack16}" if ack16 else "",
    )

    odd_anoms: list[str] = []
    for _, r in inp.cleaned:
        sels: dict[tuple[str, str], dict[str, float]] = {}
        for o in r.odds:
            key = (o["snapshot_type"], o["bookmaker"] or o["aggregate_kind"])
            sels.setdefault(key, {})[o["selection"]] = o["price"]
        for key, v in sels.items():
            if len(v) == 3:
                book = sum(1 / p for p in v.values())
                if not 0.95 <= book <= 1.35 and key[1] != "max":  # best-of-books can dip below 1
                    odd_anoms.append(f"{r.fixture.fixture_id} {key}: overround {book:.3f}")
            if any(p > 100 for p in v.values()):
                odd_anoms.append(f"{r.fixture.fixture_id} {key}: price > 100")
    odd_anoms, ack17 = _split_ack(odd_anoms, "Q17", inp)
    add(
        "Q17",
        "anomalous_odds",
        "warning",
        not odd_anoms,
        _cap(odd_anoms),
        f"acknowledged (reviewed) anomalies: {ack17}" if ack17 else "",
    )

    complete_entries = [
        e
        for e in entries
        if seasons.get((e.league, e.season)) is None
        or seasons[(e.league, e.season)].status != SeasonStatus.CURRENT_PARTIAL
    ]  # partial current-season files are legitimately small
    sizes = [e.size_bytes for e in complete_entries]
    small = [e.filename for e in complete_entries if e.size_bytes < 1024]
    med = statistics.median(sizes) if sizes else 0
    outliers = [e.filename for e in complete_entries if med and e.size_bytes < med * 0.1]
    add("Q18", "source_file_size", "error", not small, small, "non-partial files smaller than 1 KiB")
    add(
        "Q18b",
        "source_file_size_outliers",
        "warning",
        not outliers,
        outliers,
        "files below 10% of the median size (possible truncation)",
    )

    accepted = Counter((r.fixture.league_id, r.fixture.season) for _, r in inp.cleaned)
    rejected = Counter(r.get("league_season") for r in inp.rejected)
    mismatch = [
        f"{k}: raw {n} != accepted+rejected {accepted.get(k, 0) + rejected.get(k, 0)}"
        for k, n in inp.raw_rows.items()
        if n != accepted.get(k, 0) + rejected.get(k, 0)
    ]
    add("Q19", "source_row_count_reconciled", "error", not mismatch, mismatch)

    hashes = {e.filename: e.schema_hash for e in entries}
    lacking = [
        f
        for f, cols in inp.file_columns.items()
        if not {"HS", "AS"}.issubset(cols) or not {"B365H", "B365D", "B365A"}.issubset(cols)
    ]
    add(
        "Q20",
        "schema_hash_and_optional_columns",
        "warning",
        not lacking and all(hashes.values()),
        {
            "distinct_schema_hashes": len(set(hashes.values())),
            "files_without_stats_or_odds": lacking,
        },
    )
    return checks, seasons


def summarize(checks: list[CheckResult]) -> dict:
    return {
        "errors": [c.id for c in checks if not c.passed and c.severity == "error"],
        "warnings": [c.id for c in checks if not c.passed and c.severity == "warning"],
        "n_checks": len(checks),
    }


def build_report(inp: QualityInputs, checks: list[CheckResult], seasons: dict, data_version: str) -> dict:
    rej = Counter(r["reason"] for r in inp.rejected)
    invalid: Counter = Counter()
    for _, r in inp.cleaned:
        invalid.update(r.invalid)
    return {
        "data_version": data_version,
        "as_of": inp.as_of.isoformat(),
        "summary": {
            "raw_rows": sum(inp.raw_rows.values()),
            "accepted_fixtures": len(inp.cleaned),
            "rejected_rows": len(inp.rejected),
            "leagues": sorted({k[0] for k in seasons}),
            "seasons": sorted({k[1] for k in seasons}),
            **summarize(checks),
        },
        "checks": [
            {
                "id": c.id,
                "name": c.name,
                "severity": c.severity,
                "status": "pass" if c.passed else "FAIL",
                "observed": c.observed,
                "detail": c.detail,
            }
            for c in checks
        ],
        "rejected_by_reason": dict(sorted(rej.items())),
        "invalid_values_nulled": dict(sorted(invalid.items())),
        "unmatched_teams": sorted(inp.teams.review_queue.values(), key=lambda e: e["raw_name"]),
        "seasons": {
            f"{k[0]}|{k[1]}": {
                "status": v.status.value,
                "fixtures": v.n_fixtures,
                "finished": v.n_finished,
                "expected": v.expected,
                "first_date": v.first_date,
                "last_date": v.last_date,
            }
            for k, v in sorted(seasons.items())
        },
        "raw_files": [
            {
                "file": e.filename,
                "league": e.league,
                "season": e.season,
                "origin": e.origin,
                "sha256": e.sha256,
                "expected_checksum_status": e.expected_checksum_status,
                "source_url": e.source_url,
                "retrieved_at_utc": e.retrieved_at_utc,
            }
            for e in inp.manifest.entries
        ],
        "manifest_problems": list(inp.manifest.problems),
        "known_issues": [
            "Odds have no snapshot timestamp (timestamp_quality=unknown): closing odds are a "
            "REFERENCE_MARKET_BASELINE, never a time-aligned signal.",
            "Kickoff times: source gives UK local time (converted to UTC); rows without a time use "
            "00:00 UTC of the match day (kickoff_time_known=false).",
            "result_available_at_utc is INFERRED (kickoff + configured lag), not observed.",
            "No xG in this source.",
        ],
    }


def write_report(report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "quality_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    s = report["summary"]
    lines = [
        f"# Data quality report — {report['data_version']}",
        "",
        f"- as_of: {report['as_of']} | raw rows: {s['raw_rows']} | accepted: "
        f"{s['accepted_fixtures']} | rejected: {s['rejected_rows']}",
        f"- leagues: {', '.join(s['leagues'])} | seasons: {', '.join(s['seasons'])}",
        f"- failed error checks: {s['errors'] or 'none'} | failed warning checks: {s['warnings'] or 'none'}",
        "",
        "## Checks",
        "",
        "| id | check | severity | status | observed |",
        "|---|---|---|---|---|",
    ]
    for c in report["checks"]:
        obs = json.dumps(c["observed"], sort_keys=True, default=str)
        lines.append(f"| {c['id']} | {c['name']} | {c['severity']} | {c['status']} | {obs[:120]} |")
    lines += ["", "## Season status", ""]
    for k, v in report["seasons"].items():
        lines.append(
            f"- {k}: {v['status']} — {v['finished']}/{v['expected']} finished "
            f"({v['first_date']} .. {v['last_date']})"
        )
    lines += ["", "## Rejected by reason"]
    lines += [f"- {k}: {v}" for k, v in report["rejected_by_reason"].items()]
    lines += ["", "## Unmatched teams (review queue)"]
    lines += [f"- {t['raw_name']} ({t['country']}) -> {t['suggestions']}" for t in report["unmatched_teams"]]
    lines += ["", "## Manifest problems", *[f"- {p}" for p in report["manifest_problems"]]]
    lines += ["", "## Known issues", *[f"- {k}" for k in report["known_issues"]]]
    (out_dir / "quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
