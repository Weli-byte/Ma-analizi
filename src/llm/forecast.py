"""Real end-to-end LLM forecast for ONE real upcoming fixture (ADR 0024, master prompt phase 39):

    ALLOW_REAL_LLM_CALLS=true python -m src.llm.forecast --league PL [--fixture-id ID] [--dry-plan]

fixture (football-data.org, REAL) -> team resolution (existing TeamDirectory; never auto-register)
-> leakage-safe features at information_cutoff = now -> snapshot -> cutoff audit -> budget
pre-flight -> one REAL call per enabled+keyed provider (PROSPECTIVE track: refused at/after
kickoff before any call) -> immutable PredictionRecords + calls + raw responses persisted under
artifacts/llm_runs/forecast_<fixture>_<utc>/ . A provider that fails yields NO prediction and
nothing is substituted. Providers without a key are reported NOT_CONFIGURED.

Known limitation (reported, not hidden): history comes from the football-data.co.uk dataset
(`data/processed`); results after that dataset's last match are not in the features.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.cli_utils import configure_output, load_dotenv
from src.config import config_dir_for, load_config
from src.data.dataset import resolve_dataset
from src.data.teams import TeamDirectory
from src.features.builder import load_matches
from src.features.compute import compute_features
from src.features.history import MatchHistory
from src.schemas import ExperimentType, PredictionLedger
from src.schemas.lifecycle import dump_records

from .budget import BudgetExceeded, estimate_input_tokens, preflight
from .pricing import load_price_table
from .prompt import SYSTEM_PROMPT, build_user_prompt
from .providers import PROVIDERS
from .runner import ProviderNotConfigured, run_one
from .snapshot import CutoffViolation, build_snapshot

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "football-data-org"
LEAGUES = {"PL": ("EPL", "ENG"), "PD": ("LALIGA", "ESP")}  # provider code -> (repo league, country)


@dataclass(frozen=True)
class UpcomingRow:
    """A not-yet-played fixture: same read surface as `EvalRow` for `build_snapshot`, but NO
    outcome/goals exist at all."""

    fixture_id: str
    league_id: str
    season: str
    kickoff_utc: datetime
    home_id: str
    away_id: str
    features: dict[str, float | None] = field(default_factory=dict)
    unavailable_reasons: dict[str, str] = field(default_factory=dict)
    odds: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # none: no exact-ts odds


def current_season(now: datetime) -> str:
    y = now.year if now.month >= 7 else now.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def pick_fixture(raws, now: datetime, fixture_id: str | None):
    upcoming = sorted(
        (f for f in raws if f.status_raw == "NS" and f.kickoff_utc > now), key=lambda f: f.kickoff_utc
    )
    if fixture_id:
        for f in upcoming:
            if f.provider_fixture_id == fixture_id:
                return f
        raise ValueError(f"fixture {fixture_id} is not an upcoming (NS, future) fixture")
    if not upcoming:
        raise ValueError("no upcoming fixtures returned by the provider")
    return upcoming[0]


@dataclass(frozen=True)
class UpcomingBundle:
    rows: list[UpcomingRow]
    raws: list  # RawFixture, same order as rows
    now: datetime  # the information cutoff
    data_version: str
    feature_version: str
    skipped_unresolved: list[str]
    history_latest_match_utc: datetime | None


def pick_fixtures(raws, now: datetime, fixture_id: str | None, n: int) -> list:
    if fixture_id:
        return [pick_fixture(raws, now, fixture_id)]
    upcoming = sorted(
        (f for f in raws if f.status_raw == "NS" and f.kickoff_utc > now), key=lambda f: f.kickoff_utc
    )
    if not upcoming:
        raise ValueError("no upcoming fixtures returned by the provider")
    return upcoming[:n]


def load_upcoming_rows(root: Path, league: str, fixture_id: str | None = None, n: int = 1) -> UpcomingBundle:
    """Real upcoming fixtures -> team resolution (never auto-register) -> leakage-safe features at
    `information_cutoff = now`. Fixtures with an unresolved team are skipped and REPORTED; a
    requested `fixture_id` that cannot be resolved raises."""
    from src.ingestion.football_data_org import FootballDataOrgProvider

    if league not in LEAGUES:
        raise ValueError(f"league must be one of {sorted(LEAGUES)}")
    repo_league, country = LEAGUES[league]
    cdir = config_dir_for(root)
    model_cfg = load_config("model", cdir)
    data_cfg = load_config("data", cdir)
    ing_cfg = load_config("ingestion", cdir)
    now = datetime.now(UTC).replace(microsecond=0)

    fd_key = ing_cfg.api_key(SOURCE)
    if not fd_key:
        raise ProviderNotConfigured("FOOTBALL_DATA_ORG_API_KEY is not set (fixture source)")
    all_raws = FootballDataOrgProvider(fd_key).list_fixtures(league, current_season(now))
    # over-fetch candidates so unresolved teams can be skipped without returning fewer than `n`
    candidates = pick_fixtures(all_raws, now, fixture_id, n if fixture_id else max(n * 4, n))

    directory = TeamDirectory.load(cdir / "team_aliases.yaml")
    ref = resolve_dataset(root / data_cfg.processed_dir)
    matches = load_matches(ref)
    history = MatchHistory(matches)

    rows, raws, skipped = [], [], []
    for raw in candidates:
        day = raw.kickoff_utc.date()
        home = directory.resolve(SOURCE, raw.home_team_raw_name, country, day)
        away = directory.resolve(SOURCE, raw.away_team_raw_name, country, day)
        unresolved = [
            nm for nm, r in ((raw.home_team_raw_name, home), (raw.away_team_raw_name, away))
            if r.team_id is None
        ]  # fmt: skip
        if unresolved:
            if fixture_id:
                raise ValueError(
                    f"unresolved team name(s) {unresolved}: review with `python -m "
                    f"src.data.team_resolution review`, then approve (ADR 0010; never auto-registered)"
                )
            skipped.extend(unresolved)
            continue
        stub = UpcomingRow(
            f"fdorg-{raw.provider_fixture_id}", repo_league, raw.season, raw.kickoff_utc,
            home.team_id, away.team_id,
        )  # fmt: skip
        fr = compute_features(stub, history, now)  # raises if cutoff is after kickoff
        rows.append(
            UpcomingRow(
                stub.fixture_id,
                repo_league,
                raw.season,
                raw.kickoff_utc,
                home.team_id,
                away.team_id,
                dict(fr.values),
                dict(fr.reasons),
            )  # fmt: skip
        )
        raws.append(raw)
        if len(rows) >= n:
            break
    if not rows:
        raise ValueError(f"no forecastable fixture; unresolved team names: {sorted(set(skipped))}")
    return UpcomingBundle(
        rows, raws, now, ref.data_version, model_cfg.feature_version, sorted(set(skipped)),
        max((m.kickoff_utc for m in matches), default=None),
    )  # fmt: skip


def run_forecast(root: Path, league: str, fixture_id: str | None, only: list[str] | None) -> Path:
    cdir = config_dir_for(root)
    cfg = load_config("provider", cdir)
    prices = load_price_table()
    bundle = load_upcoming_rows(root, league, fixture_id, 1)
    row, raw, now = bundle.rows[0], bundle.raws[0], bundle.now
    latest_result = bundle.history_latest_match_utc

    enabled = [(n, e) for n, e in cfg.providers.items() if e.enabled and (not only or n in only)]
    statuses, runnable = [], []
    for name, entry in enabled:
        if entry.model.upper() == "TBD" or not cfg.api_key(name):
            statuses.append({"provider": name, "model": entry.model, "status": "NOT_CONFIGURED"})
        else:
            runnable.append((name, entry))

    snapshot = build_snapshot(row, now)
    in_tok = estimate_input_tokens(SYSTEM_PROMPT, build_user_prompt(snapshot))
    exposure = preflight([(n, e.model, in_tok) for n, e in runnable], cfg.budget, prices)
    print(
        f"FIXTURE {row.fixture_id} {raw.home_team_raw_name} vs {raw.away_team_raw_name} "
        f"kickoff {row.kickoff_utc.isoformat()} cutoff {now.isoformat()}\n"
        f"PRE-FLIGHT OK: {exposure.request_count} real request(s), worst-case "
        f"{exposure.token_budget} tokens, ${exposure.max_cost_usd:.6f}"
    )

    out_dir = root / "artifacts" / "llm_runs" / f"forecast_{row.fixture_id}_{now:%Y%m%dT%H%M%SZ}"
    (out_dir / "responses").mkdir(parents=True)
    ledger = PredictionLedger()
    calls, preds = [], []
    for name, entry in runnable:
        res = run_one(
            row, PROVIDERS[name], entry.model, cfg.api_key(name), ExperimentType.PROSPECTIVE,
            cfg.budget, prices, feature_version=bundle.feature_version,
            data_version=bundle.data_version, information_cutoff=now,
        )  # fmt: skip
        calls.append(res.call)
        if res.prediction is not None:
            ledger.append(res.prediction)
            preds.append(res.prediction)
        if res.raw_text is not None:
            (out_dir / "responses" / f"{name}.json").write_text(
                json.dumps({"provider": name, "request_id": res.call.request_id,
                            "raw_response_sha256": res.call.raw_response_sha256,
                            "text": res.raw_text}, indent=2), encoding="utf-8")  # fmt: skip
        statuses.append({
            "provider": name, "model": entry.model, "status": res.call.status.upper(),
            "request_id": res.call.request_id, "latency_ms": res.call.latency_ms,
            "tokens": res.call.total_tokens, "cost_usd": res.call.cost_usd,
            "retries": res.call.retries, "error_kind": res.call.error_kind,
            "p": [round(res.prediction.p_home, 3), round(res.prediction.p_draw, 3),
                  round(res.prediction.p_away, 3)] if res.prediction else None,
        })  # fmt: skip

    (out_dir / "predictions.jsonl").write_text(dump_records(preds) + "\n" if preds else "", encoding="utf-8")
    calls_text = "\n".join(c.model_dump_json() for c in calls)
    (out_dir / "calls.jsonl").write_text(calls_text + "\n", encoding="utf-8")
    (out_dir / "snapshot.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "coverage.json").write_text(json.dumps({
        "fixture": row.fixture_id, "kickoff_utc": row.kickoff_utc.isoformat(),
        "information_cutoff": now.isoformat(), "data_version": bundle.data_version,
        "feature_version": bundle.feature_version, "providers": statuses,
        "history_latest_match_utc": latest_result.isoformat() if latest_result else None,
        "note": "history = football-data.co.uk dataset; later results are not in the features",
    }, indent=2), encoding="utf-8")  # fmt: skip
    for s in statuses:
        print(json.dumps(s, sort_keys=True))
    return out_dir


def main(argv: list[str] | None = None) -> int:
    configure_output()
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--league", default="PL", choices=sorted(LEAGUES))
    p.add_argument("--fixture-id", default=None, help="provider fixture id; default: next upcoming")
    p.add_argument("--providers", default=None, help="comma list; default: all enabled in provider.yaml")
    a = p.parse_args(argv)
    if os.environ.get("ALLOW_REAL_LLM_CALLS", "").lower() != "true":
        print("REAL_CALLS_DISABLED_BY_OPERATOR: set ALLOW_REAL_LLM_CALLS=true. No API call made.",
              file=sys.stderr)  # fmt: skip
        return 4
    only = a.providers.split(",") if a.providers else None
    try:
        out = run_forecast(Path(a.root), a.league, a.fixture_id, only)
    except (BudgetExceeded, CutoffViolation, ProviderNotConfigured, ValueError, RuntimeError) as e:
        print(f"FORECAST FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"artifacts: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
