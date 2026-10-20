"""League x season coverage matrix and the training-universe decision (Sprint 1, plan tasks 2 and 6).

Reads ONLY the cached public-domain source files (openfootball) listed in the licence manifest as PERMITTED;
the research CSVs of football-data.co.uk (BLOCKED for league models) are listed as excluded and never
opened. Everything is computed from the real cached bytes: no percentage is typed in. A figure the source
cannot supply is 0.0 with the reason; one that could be supplied but was not measured is None (UNKNOWN).
Counts never look at goal values, except the plain range check of the quality report."""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.data.teams import TeamDirectory
from src.ingestion import openfootball, openfootball_cups

from .asof import HistoryMatch, inferred_available_at
from .competitions import Competition, LeagueConfig, expected_matches, format_version, season_label
from .identity import IdentityRow, Unresolved, collision_audit, cup_rows, domestic_rows
from .licenses import SourceLicense, gate_reasons

CACHE = Path("artifacts") / "markets" / "_cache"


@dataclass(frozen=True)
class SourceFile:
    competition: str
    season: str
    provider_id: str
    path: str  # relative to the repository root
    sha256: str
    bytes: int


@dataclass
class SeasonCoverage:
    canonical_league_id: str
    repo_key: str
    season: str
    provider_id: str
    competition_format_version: str
    source_status: str  # OK | FILE_MISSING
    source_note: str
    source_file: str | None
    source_file_sha256: str | None
    fixture_count: int = 0
    completed_count: int = 0
    due_count: int = 0  # fixtures whose kick-off + lag is at or before the audit time
    future_scheduled_count: int = 0
    resolved_completed_count: int = 0
    unresolved_fixture_count: int = 0
    published_through_utc: str | None = None  # kick-off of the latest fixture that has a score
    inner_missing_count: int = 0  # fixtures without a score that kicked off BEFORE published_through
    unpublished_tail_count: int = (
        0  # due fixtures without a score after published_through (lag, cancellation or gap)
    )
    season_over: bool = False  # every fixture of the file is due
    training_missingness_ratio: float | None = (
        None  # the ratio the training decision uses (see season_decision)
    )
    first_kickoff_utc: str | None = None
    last_kickoff_utc: str | None = None
    expected_matches: int | None = None
    result_coverage: float | None = None  # completed / due
    missingness_ratio: float | None = None  # 1 - result_coverage
    identity_coverage: float | None = None  # finished fixtures with both clubs resolved / finished fixtures
    kickoff_time_known_ratio: float | None = None
    shot_coverage: float | None = None
    xg_coverage: float | None = None
    lineups_coverage: float | None = None
    odds_coverage: float | None = None
    available_at_coverage: float | None = None  # share of results with an OBSERVED availability time
    available_at_source: str = "inferred"
    license_status: str = "UNKNOWN"
    global_benchmark_final_test_season: bool = (
        False  # configs/evaluation.yaml locks it for the global research benchmark
    )
    training_status: str = "BLOCKED_NO_DATA"
    training_reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    as_of: datetime
    coverage: list[SeasonCoverage]
    identity_rows: list[IdentityRow]
    unresolved: list[Unresolved]
    source_files: list[SourceFile]
    excluded_files: list[dict]
    quality: dict


def last_season_year(as_of: datetime) -> int:
    return as_of.year if as_of.month >= 7 else as_of.year - 1


def _season_file(root: Path, comp: Competition, year: int) -> Path:
    if comp.kind == "domestic":
        return root / CACHE / "openfootball" / openfootball.season_dir(year) / f"{comp.file_code}.json"
    return root / CACHE / "openfootball_cups" / f"{comp.repo_key}_{year}.txt"


def _supplies_ratio(lic: SourceLicense, kind: str) -> float | None:
    """0.0 if the source never supplies `kind`; None (unknown) if it could but it was not measured."""
    return None if kind in lic.supplies else 0.0


def _missing_note(comp: Competition, year: int) -> str:
    if comp.kind == "uefa":
        first = openfootball_cups.FIRST_SEASON[comp.repo_key]
        last = openfootball_cups.LAST_SEASON[comp.repo_key]
        if year < first or year > last:
            return (
                f"outside the range the source repository publishes ({first} to {last}, checked 2026-10-13)"
            )
        return "inside the published range but not in the local cache"
    return "no cached file (the source answered 404 or the season was never fetched)"


def season_decision(row: SeasonCoverage, lic: SourceLicense, cfg: LeagueConfig) -> tuple[str, list[str]]:
    """Training status of one league season. Explicit, ordered, and every blocker is listed."""
    license_problems = gate_reasons(lic, "production_ml")
    if license_problems:
        return "BLOCKED_LICENSE", license_problems
    if row.source_status != "OK" or row.fixture_count == 0:
        return "BLOCKED_NO_DATA", [row.source_note]
    if row.due_count == 0 or row.completed_count == 0:
        return "BLOCKED_NO_COMPLETED_MATCHES", ["no fixture is due yet or none has a published score"]
    blockers = []
    ratio = row.training_missingness_ratio
    if ratio is not None and ratio > cfg.max_missingness_for_training:
        scope = "of the fixtures" if row.season_over else "of the fixtures up to the latest published score"
        blockers.append(f"{ratio:.1%} {scope} lack a score (limit {cfg.max_missingness_for_training:.0%})")
    if row.identity_coverage is not None and row.identity_coverage < cfg.min_identity_coverage:
        blockers.append(
            f"identity coverage {row.identity_coverage:.1%} (limit {cfg.min_identity_coverage:.0%})"
        )
    if blockers:
        return "BLOCKED_INCOMPLETE_SOURCE", blockers
    return ("TRAINABLE_PARTIAL_CURRENT" if row.future_scheduled_count else "TRAINABLE_COMPLETE"), []


def collect(
    root: Path,
    cfg: LeagueConfig,
    directory: TeamDirectory,
    manifest: dict[str, SourceLicense],
    as_of: datetime,
) -> AuditResult:
    root = Path(root)
    lag = timedelta(hours=cfg.result_lag_hours)
    locked = _final_test_seasons(root)
    coverage: list[SeasonCoverage] = []
    all_rows: list[IdentityRow] = []
    unresolved: list[Unresolved] = []
    files: list[SourceFile] = []
    for comp in cfg.competitions.values():
        lic = manifest[comp.source]
        for year in range(cfg.history_start_year, last_season_year(as_of) + 1):
            season = season_label(year)
            path = _season_file(root, comp, year)
            row = SeasonCoverage(
                comp.canonical_id,
                comp.repo_key,
                season,
                comp.source,
                format_version(cfg, comp.canonical_id, year),
                "FILE_MISSING",
                _missing_note(comp, year),
                None,
                None,
                expected_matches=expected_matches(cfg, comp.canonical_id, year),
                shot_coverage=_supplies_ratio(lic, "shots"),
                xg_coverage=_supplies_ratio(lic, "xg"),
                lineups_coverage=_supplies_ratio(lic, "lineups"),
                odds_coverage=_supplies_ratio(lic, "odds"),
                license_status=lic.plan_status,
                global_benchmark_final_test_season=season in locked and comp.repo_key in ("EPL", "LALIGA"),
            )
            if comp.first_season is not None and year < comp.first_season:
                row.source_status = "NOT_APPLICABLE"
                row.source_note = (
                    f"the competition did not exist before the {season_label(comp.first_season)} season"
                )
                row.training_status = "NOT_APPLICABLE_NOT_YET_EXISTING"
                row.competition_format_version = "NOT_APPLICABLE"
                coverage.append(row)
                continue
            if path.is_file():
                raw = path.read_bytes()
                rel = path.relative_to(root).as_posix()
                sha = hashlib.sha256(raw).hexdigest()
                row.source_status, row.source_note, row.source_file, row.source_file_sha256 = (
                    "OK",
                    "",
                    rel,
                    sha,
                )
                files.append(SourceFile(comp.canonical_id, season, comp.source, rel, sha, len(raw)))
                builder = domestic_rows if comp.kind == "domestic" else cup_rows
                rows, bad = builder(comp, year, raw, directory, rel)
                _fill(row, rows, bad, raw, comp, year, lag, as_of)
                all_rows.extend(rows)
                unresolved.extend(bad)
                row.available_at_coverage = 0.0  # these sources publish no result timestamp
            else:
                row.available_at_coverage = None
            row.training_status, row.training_reasons = season_decision(row, lic, cfg)
            row.flags = _flags(row, cfg)
            coverage.append(row)
    return AuditResult(
        as_of,
        coverage,
        all_rows,
        unresolved,
        files,
        _excluded_files(root),
        quality_report(all_rows, as_of, lag),
    )


def _simple(raw: bytes, comp: Competition, year: int) -> list[tuple[datetime, bool, bool]]:
    """(kickoff_utc, finished, kickoff_time_known) of every fixture in the file, whether or not its clubs
    resolve."""
    if comp.kind == "domestic":
        return [
            (f.kickoff_utc, f.home_goals is not None, bool(f.extra.get("time_given")))
            for f in openfootball.parse(raw, comp.repo_key, season_label(year))
        ]
    return [
        (m.kickoff_utc, bool(m.goals), m.time_known)
        for m in openfootball_cups.parse(raw.decode("utf-8"), comp.repo_key, year)
    ]


def _fill(
    row: SeasonCoverage,
    rows: list[IdentityRow],
    bad: list[Unresolved],
    raw: bytes,
    comp: Competition,
    year: int,
    lag: timedelta,
    as_of: datetime,
) -> None:
    """Counts over EVERY fixture of the file (resolved or not); identity coverage compares them with the
    resolved rows."""
    simple = _simple(raw, comp, year)
    row.fixture_count = len(simple)
    row.completed_count = sum(1 for _, finished, _ in simple if finished)
    row.due_count = sum(1 for ko, _, _ in simple if ko + lag <= as_of)
    row.future_scheduled_count = sum(1 for ko, finished, _ in simple if not finished and ko + lag > as_of)
    completed_due = sum(1 for ko, finished, _ in simple if finished and ko + lag <= as_of)
    if simple:
        kickoffs = sorted(ko for ko, _, _ in simple)
        row.first_kickoff_utc, row.last_kickoff_utc = kickoffs[0].isoformat(), kickoffs[-1].isoformat()
        row.kickoff_time_known_ratio = round(sum(1 for _, _, known in simple if known) / len(simple), 6)
    if row.due_count:
        row.result_coverage = round(completed_due / row.due_count, 6)
        row.missingness_ratio = round(1 - completed_due / row.due_count, 6)
    finished_kickoffs = [ko for ko, finished, _ in simple if finished]
    through = max(finished_kickoffs) if finished_kickoffs else None
    row.season_over = bool(simple) and row.future_scheduled_count == 0 and row.due_count == row.fixture_count
    if through is not None:
        row.published_through_utc = through.isoformat()
        row.inner_missing_count = sum(1 for ko, finished, _ in simple if not finished and ko < through)
        row.unpublished_tail_count = sum(
            1 for ko, finished, _ in simple if not finished and ko >= through and ko + lag <= as_of
        )
        inner_total = sum(1 for ko, _, _ in simple if ko <= through)
        # A finished season is judged on ALL its fixtures (an unscored tail is a gap or a cancelled match:
        # the data cannot tell). A running season is judged on the fixtures up to the latest published score
        # (the tail is publication lag).
        if row.season_over:
            row.training_missingness_ratio = row.missingness_ratio
        elif inner_total:
            row.training_missingness_ratio = round(row.inner_missing_count / inner_total, 6)
    row.resolved_completed_count = sum(1 for r in rows if r.status == "FINISHED")
    row.unresolved_fixture_count = row.fixture_count - len(rows)
    if row.completed_count:
        row.identity_coverage = round(row.resolved_completed_count / row.completed_count, 6)


def _flags(row: SeasonCoverage, cfg: LeagueConfig) -> list[str]:
    flags = []
    if row.source_status == "OK":
        flags.append("BACKTEST_LIMITED")  # result availability is inferred (kick-off + lag), never observed
    if row.kickoff_time_known_ratio is not None and row.kickoff_time_known_ratio < cfg.min_kickoff_time_known:
        flags.append("KICKOFF_TIME_UNRELIABLE")
    if row.competition_format_version == "UNKNOWN_FORMAT":
        flags.append("FORMAT_UNKNOWN")
    if row.expected_matches is not None and row.fixture_count and row.fixture_count != row.expected_matches:
        flags.append(f"FIXTURE_COUNT_{row.fixture_count}_VS_DECLARED_{row.expected_matches}")
    if row.unresolved_fixture_count:
        flags.append("UNRESOLVED_CLUBS")
    if row.unpublished_tail_count:
        kind = "UNSCORED_TAIL_OF_FINISHED_SEASON" if row.season_over else "RESULTS_LAG_TAIL"
        flags.append(f"{kind}_{row.unpublished_tail_count}")
    if row.inner_missing_count:
        flags.append(f"RESULT_GAPS_INSIDE_PUBLISHED_RANGE_{row.inner_missing_count}")
    if row.global_benchmark_final_test_season:
        flags.append("GLOBAL_BENCHMARK_FINAL_TEST_SEASON")
    return flags


def _final_test_seasons(root: Path) -> set[str]:
    import yaml

    path = root / "configs" / "evaluation.yaml"
    if not path.is_file():
        return set()
    return set(yaml.safe_load(path.read_text(encoding="utf-8")).get("final_test_seasons", []))


def _excluded_files(root: Path) -> list[dict]:
    """Local research files of BLOCKED sources: listed with name and size only, never opened."""
    out = []
    folder = root / "data" / "raw" / "football_data"
    if folder.is_dir():
        for p in sorted(folder.glob("*.csv")):
            out.append(
                {
                    "provider_id": "football_data_co_uk",
                    "path": p.relative_to(root).as_posix(),
                    "bytes": p.stat().st_size,
                    "reason": "plan_status BLOCKED for league models; not opened, not hashed, not counted",
                }
            )
    return out


def quality_report(rows: list[IdentityRow], as_of: datetime, lag: timedelta) -> dict:
    """Plan task 6 checks on the real mapping rows (counts only)."""
    finished = [r for r in rows if r.final_score is not None]
    return {
        "rows": len(rows),
        "finished_rows": len(finished),
        "collisions": collision_audit(rows),
        "score_out_of_range_0_to_15": sum(
            1 for r in finished if not all(0 <= g <= 15 for g in r.final_score)
        ),
        "finished_with_kickoff_in_the_future": sum(1 for r in finished if r.kickoff_utc > as_of),
        "result_available_before_audit_time": sum(1 for r in finished if r.kickoff_utc + lag <= as_of),
        "kickoff_time_unknown_rows": sum(1 for r in rows if not r.kickoff_time_known),
        "timezone_note": "kick-off times are local clock times without a zone, converted to UTC by the "
        "league zone (INFERRED); a DST gap or fold falls back to the date only",
    }


def history_matches(rows: list[IdentityRow], lag_hours: int) -> list[HistoryMatch]:
    """Finished fixtures as time-audit inputs. Availability is INFERRED: kick-off + lag (ADR 0006)."""
    return [
        HistoryMatch(
            f"{r.canonical_league_id}|{r.canonical_fixture_id}",
            r.kickoff_utc,
            inferred_available_at(r.kickoff_utc, lag_hours),
        )
        for r in rows
        if r.final_score is not None
    ]


def universe_summary(coverage: list[SeasonCoverage]) -> list[dict]:
    """Per competition: trainable seasons, completed matches in them, and the plan's data-volume tier (page 2
    table)."""
    out = []
    for comp_id in dict.fromkeys(c.canonical_league_id for c in coverage):
        rows = [c for c in coverage if c.canonical_league_id == comp_id]
        trainable = [c for c in rows if c.training_status.startswith("TRAINABLE")]
        total = sum(c.completed_count for c in trainable)
        if total < 500:
            tier = "UNDER_500: global + league indicator + regularisation; no independent league model"
        elif total <= 1500:
            tier = "500_TO_1500: global + league effect; local DC/Elo candidates only with OOS superiority"
        else:
            tier = "OVER_1500: local GBM, DC and hierarchical candidates, compare with nested walk-forward"
        out.append(
            {
                "canonical_league_id": comp_id,
                "trainable_seasons": [c.season for c in trainable],
                "blocked_seasons": {c.season: c.training_status for c in rows if c not in trainable},
                "completed_matches_in_trainable_seasons": total,
                "plan_volume_tier": tier,
                "note": "UEFA: tournament sample is small; a cross-league bridge is the default (plan page 2)"
                if comp_id.startswith("UEFA")
                else "",
            }
        )
    return out


def coverage_dicts(coverage: list[SeasonCoverage]) -> list[dict]:
    return [asdict(c) for c in coverage]


def utc_now() -> datetime:
    return datetime.now(UTC)
