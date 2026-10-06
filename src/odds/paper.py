"""Paper-trading ledger (ADR 0030). NOT real betting: abstract stake units, immutable records.

`PaperBet` is created at most once per (fixture, model, selection) -- the first eligible decision
stands, a later better price never replaces it. A `PaperSettlement` is a separate, later record
(win/lose, profit in units, CLV vs the last exact pre-kickoff observation when one exists).
Reports include the sample size and say plainly when it is too small to mean anything.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
from pydantic import Field

from src.schemas.common import ImmutableModel, UtcDatetime

from .math import clv
from .value import SELECTIONS, ValueRow

MIN_BETS_FOR_ROI = 30  # below this a ROI/CLV figure is noise and the report says so


class PaperBet(ImmutableModel):
    bet_id: str
    fixture_id: str
    model_id: str
    selection: str
    odds_taken: float = Field(gt=1.0)
    stake_units: float = Field(gt=0)
    edge: float
    ev: float
    bookmaker: str
    placed_at: UtcDatetime  # the quote's observation time
    kickoff_utc: UtcDatetime


class PaperSettlement(ImmutableModel):
    bet_id: str
    outcome: str  # H | D | A
    won: bool
    profit_units: float
    clv: float | None  # None when no exact pre-kickoff closing observation exists
    settled_at: UtcDatetime


def bet_id_for(fixture_id: str, model_id: str, selection: str) -> str:
    return hashlib.sha256(f"{fixture_id}|{model_id}|{selection}".encode()).hexdigest()[:16]


class PaperLedger:
    def __init__(self, root: Path):
        self.dir = Path(root) / "artifacts" / "odds"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.bets_path = self.dir / "paper_bets.jsonl"
        self.settle_path = self.dir / "paper_settlements.jsonl"

    def _read(self, path: Path, cls):
        if not path.exists():
            return []
        return [
            cls.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()
        ]

    def bets(self) -> list[PaperBet]:
        return self._read(self.bets_path, PaperBet)

    def settlements(self) -> list[PaperSettlement]:
        return self._read(self.settle_path, PaperSettlement)

    def place(
        self, row: ValueRow, kickoff_utc, min_edge: float, min_ev: float, stake: float
    ) -> PaperBet | None:
        """The single best-EV selection of an ELIGIBLE row, if it clears both thresholds."""
        if row.status != "ELIGIBLE":
            return None
        i = max(range(3), key=lambda k: row.ev[k])
        if row.edge[i] < min_edge or row.ev[i] < min_ev:
            return None
        sel = SELECTIONS[i]
        bid = bet_id_for(row.fixture_id, row.model_id, sel)
        if any(b.bet_id == bid for b in self.bets()):
            return None  # first decision stands
        bet = PaperBet(
            bet_id=bid, fixture_id=row.fixture_id, model_id=row.model_id, selection=sel,
            odds_taken=row.odds[i], stake_units=stake, edge=row.edge[i], ev=row.ev[i],
            bookmaker=row.bookmaker, placed_at=row.observed_at, kickoff_utc=kickoff_utc,
        )  # fmt: skip
        with self.bets_path.open("a", encoding="utf-8") as f:
            f.write(bet.model_dump_json() + "\n")
        return bet

    def settle(self, bet: PaperBet, outcome: str, closing_odds, settled_at) -> PaperSettlement:
        if outcome not in SELECTIONS:
            raise ValueError("outcome must be H, D or A")
        if any(s.bet_id == bet.bet_id for s in self.settlements()):
            raise ValueError(f"bet {bet.bet_id} is already settled")
        won = outcome == bet.selection
        profit = bet.stake_units * (bet.odds_taken - 1.0) if won else -bet.stake_units
        i = SELECTIONS.index(bet.selection)
        c = clv(bet.odds_taken, np.asarray(closing_odds), i) if closing_odds is not None else None
        s = PaperSettlement(
            bet_id=bet.bet_id, outcome=outcome, won=won, profit_units=profit, clv=c, settled_at=settled_at
        )
        with self.settle_path.open("a", encoding="utf-8") as f:
            f.write(s.model_dump_json() + "\n")
        return s


def summarize(bets: list[PaperBet], settlements: list[PaperSettlement]) -> dict:
    by = {s.bet_id: s for s in settlements}
    done = [(b, by[b.bet_id]) for b in bets if b.bet_id in by]
    staked = sum(b.stake_units for b, _ in done)
    profit = sum(s.profit_units for _, s in done)
    clvs = [s.clv for _, s in done if s.clv is not None]
    n = len(done)
    return {
        "bets_placed": len(bets),
        "bets_settled": n,
        "open": len(bets) - n,
        "profit_units": round(profit, 4),
        "roi": round(profit / staked, 4) if staked else None,
        "mean_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "n_with_clv": len(clvs),
        "reliable": n >= MIN_BETS_FOR_ROI,
        "note": "paper units only; "
        + (
            "sample large enough to read cautiously"
            if n >= MIN_BETS_FOR_ROI
            else f"fewer than {MIN_BETS_FOR_ROI} settled bets: ROI/CLV are noise, not evidence"
        ),
    }


def dumps(summary: dict) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)
