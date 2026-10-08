"""API service layer (S18, ADR 0035): the ONLY thing the HTTP layer talks to. It reads the same real
artifacts as the dashboard and never exposes storage structures directly. Every record keeps its
timestamps, model_version, data_version, feature_version and source; unknown stays None."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.config import config_dir_for, load_config
from src.dashboard.viewmodel import matches_and_predictions, models_and_calibration
from src.odds.picks import make_picks
from src.odds.store import OddsStore
from src.odds.value import value_row


def fixture_id_of(m: dict) -> str:
    return f"{m['home_id']}__{m['away_id']}__{m['kickoff_utc'][:10]}"


@dataclass(frozen=True)
class _Pred:
    model_id: str
    generated_at: datetime
    p_home: float
    p_draw: float
    p_away: float


def _public_fixture(m: dict) -> dict:
    return {
        "fixture_id": fixture_id_of(m), "league": m["league"], "kickoff_utc": m["kickoff_utc"],
        "home": {"team_id": m["home_id"], "name": m["home"]},
        "away": {"team_id": m["away_id"], "name": m["away"]},
        "upcoming": m["upcoming"], "stages_locked": sorted(m["stages"]),
        "availability": {"injuries": m["injuries"], "lineups": m["lineups"]},
        "odds": m["odds"],
    }  # fmt: skip


def _public_prediction(p: dict) -> dict:
    return {
        "prediction_id": p["prediction_id"], "model_id": p["model_id"], "model_class": p["model_class"],
        "provider": p["provider"], "model_version": p["model_version"],
        "prompt_version": p["prompt_version"],
        "probabilities": {"home": p["p"][0], "draw": p["p"][1], "away": p["p"][2]},
        "generated_at": p["generated_at"], "information_cutoff": p["information_cutoff"],
        "data_version": p["data_version"], "feature_version": p["feature_version"],
        "status": p["status"], "source": p["source"],
    }  # fmt: skip


def _result(r: dict) -> dict:
    brier = r["ci"].get("brier", {})
    return {k: v for k, v in r.items() if k != "ci"} | {
        "brier_ci95": [brier.get("lower"), brier.get("upper")]
    }


class ForecastService:
    def __init__(self, root: Path, now_fn):
        self.root = Path(root)
        self.now_fn = now_fn

    def _load(self):
        return matches_and_predictions(self.root, self.now_fn())

    def fixtures(self, upcoming_only: bool, league: str | None) -> list[dict]:
        matches, _, _ = self._load()
        return [
            _public_fixture(m)
            for m in matches
            if (not upcoming_only or m["upcoming"]) and (league is None or m["league"] == league)
        ]

    def fixture(self, fixture_id: str) -> dict | None:
        matches, _, updates = self._load()
        for m in matches:
            if fixture_id_of(m) == fixture_id:
                return {
                    **_public_fixture(m),
                    "forecast_updates": [u for u in updates if u["match"] == m["key"]],
                }
        return None

    def predictions(self, fixture_id: str) -> list[dict] | None:
        matches, preds, _ = self._load()
        for m in matches:
            if fixture_id_of(m) == fixture_id:
                return [_public_prediction(p) for p in preds if p["match"] == m["key"]]
        return None

    def team_forecast(self, team_id: str) -> dict | None:
        matches, preds, _ = self._load()
        mine = [m for m in matches if team_id in (m["home_id"], m["away_id"])]
        if not mine:
            return None
        out = []
        for m in mine:
            latest: dict[str, dict] = {}
            for p in sorted((p for p in preds if p["match"] == m["key"]), key=lambda p: p["generated_at"]):
                latest[p["model_id"]] = p  # the newest immutable record per model
            out.append(
                {**_public_fixture(m), "latest_predictions": [_public_prediction(p) for p in latest.values()]}
            )
        return {"team_id": team_id, "fixtures": out}

    def models(self) -> list[dict]:
        _, preds, _ = self._load()
        seen: dict[str, dict] = {}
        for p in preds:
            seen[p["model_id"]] = {
                "model_id": p["model_id"], "model_class": p["model_class"], "provider": p["provider"],
                "model_version": p["model_version"], "last_prediction_at": p["generated_at"],
            }  # fmt: skip
        return sorted(seen.values(), key=lambda r: r["model_id"])

    def benchmarks(self) -> dict:
        mo = models_and_calibration(self.root)
        if not mo["available"]:
            return {"available": False, "reason": mo["reason"]}
        return {
            "available": True, "run": mo["run"], "track": "HISTORICAL", "caveat": mo["caveat"],
            "n_per_model": mo["n_per_model"],
            "results": [_result(r) for r in mo["models"]],
            "calibration": mo["calibration"],
        }  # fmt: skip

    def value_research(self) -> dict:
        """PAPER-ONLY research rows. Only complete EXACT-timestamp quote sets produce edge/EV (ADR 0030)."""
        matches, preds, _ = self._load()
        rows = []
        for m in matches:
            fid = fixture_id_of(m)
            quotes = []
            for base in ("odds", "odds_remote"):
                for meta_path in (self.root / "artifacts" / base).glob("*/meta.json"):
                    store = OddsStore(self.root, meta_path.parent.name, base)
                    meta = store.meta()
                    if (meta["home_id"], meta["away_id"], meta["kickoff_utc"][:10]) == (
                        m["home_id"], m["away_id"], m["kickoff_utc"][:10]
                    ):  # fmt: skip
                        quotes += store.quotes()
            if not quotes:
                continue
            kickoff = datetime.fromisoformat(m["kickoff_utc"])
            for p in (p for p in preds if p["match"] == m["key"]):
                pred = _Pred(p["model_id"], datetime.fromisoformat(p["generated_at"]), *p["p"])
                r = value_row(pred, quotes, kickoff, fid)
                rows.append(
                    {
                        "fixture_id": fid,
                        "model_id": r.model_id,
                        "prediction_id": p["prediction_id"],
                        "status": r.status,
                        "reason": r.reason,
                        "bookmaker": r.bookmaker,
                        "observed_at": r.observed_at.isoformat() if r.observed_at else None,
                        "odds": r.odds,
                        "model_probs": r.model_probs,
                        "market_probs_devig": r.market_probs_devig,
                        "overround": r.overround,
                        "edge": r.edge,
                        "ev": r.ev,
                        "source_latency_s": r.source_latency_s,
                    }  # fmt: skip
                )
        return {"paper_only": True, "disclaimer": "Research output, not betting advice.", "rows": rows}

    def value_picks(self, min_edge: float | None = None, min_ev: float | None = None) -> dict:
        """Bet suggestions (ADR 0039): exact-odds value rows that reach the thresholds, with a capped
        fractional-Kelly stake hint. Research output, not a guarantee."""
        cfg = load_config("odds", config_dir_for(self.root))
        picks = make_picks(
            self.value_research()["rows"],
            cfg.min_edge if min_edge is None else min_edge,
            cfg.min_ev if min_ev is None else min_ev,
            cfg.kelly_fraction, cfg.max_stake_pct,
        )  # fmt: skip
        return {
            "thresholds": {"min_edge": cfg.min_edge if min_edge is None else min_edge,
                           "min_ev": cfg.min_ev if min_ev is None else min_ev},
            "n": len(picks), "picks": picks,
            "note": (
                "Only fixtures with complete exact-timestamp odds can appear here. "
                "No pick does not mean no bet is good; it means none is supported by the data."
            ),
        }  # fmt: skip
