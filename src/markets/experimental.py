"""Experimental goal-market model shown to users next to the main forecast (ADR 0072).

It is the Sprint 4c hierarchical Poisson / Dixon-Coles model (`league_models.per_league.HierarchicalDC`: team < country < world random effects plus competition terms, the variance
components chosen per target by nested walk-forward) fitted on the SAME history the product already uses (`StatMatch` rows available at `now`). It supplies over/under, BTTS, expected goals
and the most likely scorelines. It does NOT supply the 1X2 headline: on the development folds it was better than the production goal models for totals and BTTS and worse than the
production Elo for the result. Neutral-venue matches (cup finals) are skipped because the model has no home-advantage switch.

Nothing is guessed: a fit that fails returns None and the artifact simply has no experimental block (the reason is logged by the caller)."""

import hashlib
import os
from datetime import datetime

import numpy as np

from src.evaluation.dataset import EvalRow

LABEL = "Deneysel model (beta)"
VERSION = "hier-dc-1"
LINES = (1.5, 2.5, 3.5)
EVIDENCE = (
    "Geçmiş (geliştirme) maçlarında üst/alt ve karşılıklı gol tahminlerinde mevcut modelden daha iyi çıktı; maç sonucunda (1-X-2) daha iyi değil. "
    "Henüz canlı maç sonuçlarıyla doğrulanmadı."
)


def _rows(matches, now: datetime, max_seasons: int) -> list[EvalRow]:
    seasons = sorted({m.season for m in matches if m.available_at <= now}, key=lambda s: int(s.split("-")[0]) if "-" in s else int(s[:4]))
    keep = set(seasons[-max_seasons:]) if max_seasons else set(seasons)
    out = []
    for m in matches:
        if m.available_at > now or m.season not in keep:
            continue
        hg, ag = int(m.home_goals), int(m.away_goals)
        out.append(EvalRow(m.fixture_id, m.league, m.season, m.kickoff_utc, m.home_id, m.away_id, 0 if hg > ag else (1 if hg == ag else 2), home_goals=hg, away_goals=ag))
    return sorted(out, key=lambda r: (r.kickoff_utc, r.fixture_id))


class ExperimentalModel:
    def __init__(self, hier, rows: int, seasons: list[str]) -> None:
        self.hier, self.rows, self.seasons = hier, rows, seasons

    @classmethod
    def fit(cls, matches, now: datetime, lag_hours: int = 3) -> tuple["ExperimentalModel | None", str]:
        """(model, status). status is "OK" or the reason there is no model."""
        if os.environ.get("MARKETS_EXPERIMENTAL", "1") == "0":  # the owner's switch: new artifacts then carry no experimental block
            return None, "DISABLED (MARKETS_EXPERIMENTAL=0)"
        from src.league_models.per_league import HierarchicalDC
        from src.league_models.s4c_benchmark import load_s4c
        from src.league_models.stat_models import DCSpec, NotTrained

        s4c = load_s4c()
        prod = s4c["production_experimental"]
        rows = _rows(matches, now, int(prod["max_seasons"]))
        if len(rows) < int(prod["min_rows"]):
            return None, f"NOT_ENOUGH_HISTORY ({len(rows)} rows < {prod['min_rows']})"
        cfg = {**s4c, "hierarchical_dc": {"grid": prod["grid"]}}
        uefa = sum(1 for r in rows if r.league_id.startswith(("UCL", "UEL", "UECL", "UEFA")))
        try:
            hier = HierarchicalDC(cfg, DCSpec(), 2.5, lag_hours, bridge=uefa >= int(prod["min_cross_country_rows"])).fit(rows)
        except NotTrained as e:
            return None, f"NOT_TRAINED ({e})"
        return cls(hier, len(rows), sorted({r.season for r in rows})), "OK"

    def predict(self, home: str, away: str, league: str, kickoff: datetime, neutral: bool = False) -> dict | None:
        if neutral:
            return None
        row = EvalRow("exp", league, "", kickoff, home, away, 1)
        try:
            ou = self.hier.predict([row], "over_under")
            bt = self.hier.predict([row], "btts")
            sc = self.hier.predict([row], "1x2")
        except Exception:  # a prediction that fails is simply not shown
            return None
        m = ou.matrices[0]
        g = m.shape[1]
        order = np.argsort(-sc.matrices[0].ravel())[:6]
        per = self.hier.info["per_target"]
        return {
            "model": "H_dc_hier", "version": VERSION, "label": LABEL, "evidence": EVIDENCE,
            "over_under": [{"line": ln, "over": round(float(ou.over(ln)[0]), 4), "under": round(1 - float(ou.over(ln)[0]), 4)} for ln in LINES],
            "btts_yes": round(float(bt.btts()[0]), 4), "btts_no": round(1 - float(bt.btts()[0]), 4),
            "expected_goals": {"home": round(float(ou.lam_home[0]), 3), "away": round(float(ou.lam_away[0]), 3), "total": round(float(ou.lam_home[0] + ou.lam_away[0]), 3)},
            "scorelines": [{"score": f"{i // g}-{i % g}", "p": round(float(sc.matrices[0].ravel()[i]), 4)} for i in order],
            "unseen_club": bool(not (ou.seen_home[0] and ou.seen_away[0])),
            "fitted_on": {"rows": self.rows, "seasons": self.seasons[:1] + self.seasons[-1:]},
            "selected": {t: per[t]["chosen"] for t in per},
            "fit_digest": hashlib.sha256(repr(sorted((t, per[t]["parameter_digest"]) for t in per)).encode()).hexdigest()[:12],
        }
