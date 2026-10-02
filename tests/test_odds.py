"""S15 odds / edge / EV / CLV with the timestamp gate (ADR 0030). Quotes come from a REAL ESPN
(DraftKings) capture; forecasts are the REAL LLM predictions for Arsenal v Leeds generated BEFORE
that capture. Arithmetic checks use independent hand computations."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from src.odds.espn import parse_event
from src.odds.math import clv, devig, edge, ev, implied_probs, overround
from src.odds.paper import MIN_BETS_FOR_ROI, PaperLedger, summarize
from src.odds.quotes import OddsQuote, american_to_decimal
from src.odds.store import OddsStore
from src.odds.value import closing_reference, value_row
from src.schemas import PredictionRecord

CAP = Path(__file__).parent / "fixtures" / "real_provider_captures"
CAPTURE = json.loads((CAP / "espn_eng1_scoreboard.json").read_text(encoding="utf-8"))
OBSERVED = datetime.fromisoformat(CAPTURE["captured_at_utc"])
RAW = {e["id"]: e for e in CAPTURE["events"]}
PREDS = [
    PredictionRecord.from_json(x)
    for x in (CAP / "forecast_arsenal_leeds_predictions.jsonl").read_text(encoding="utf-8").splitlines()
    if x.strip()
]


def arsenal():
    return parse_event(RAW["401879268"], OBSERVED, "sha")


# ----------------------------------------------------------------------------------- quotes
def test_american_to_decimal_known_values_and_invalid_input():
    assert american_to_decimal("+390") == pytest.approx(4.9)
    assert american_to_decimal("-260") == pytest.approx(1 + 100 / 260)
    assert american_to_decimal(650) == pytest.approx(7.5)
    for bad in ("abc", "0", "+50", "-50"):
        with pytest.raises(ValueError):
            american_to_decimal(bad)


def test_real_espn_event_gives_exact_current_quotes_and_unknown_quality_opening_quotes():
    ev_ = arsenal()
    assert ev_["fixture_id"] == "espn-401879268" and ev_["home_name"] == "Arsenal"
    cur = [q for q in ev_["quotes"] if q.snapshot_type == "pre_match"]
    opn = [q for q in ev_["quotes"] if q.snapshot_type == "opening"]
    assert {q.selection for q in cur} == {"H", "D", "A"} and all(q.timestamp_quality == "exact" for q in cur)
    assert all(q.timestamp_quality == "unknown" for q in opn)  # the source gives no open time
    assert all(q.bookmaker == "DraftKings" and q.source_latency_s is None for q in ev_["quotes"])
    draw = next(q for q in cur if q.selection == "D")
    assert draw.raw_price == "+390" and draw.decimal_odds == pytest.approx(4.9)


def test_an_opening_price_cannot_be_declared_exact():
    q = arsenal()["quotes"][0]
    with pytest.raises(ValidationError):
        OddsQuote(
            **{**json.loads(q.model_dump_json()), "snapshot_type": "opening", "timestamp_quality": "exact"}
        )


def test_store_deduplicates_quotes_and_roundtrips(tmp_path):
    store = OddsStore(tmp_path, "espn-401879268")
    quotes = arsenal()["quotes"]
    assert store.add(quotes) == len(quotes) and store.add(quotes) == 0
    assert sorted(q.quote_id for q in store.quotes()) == sorted(q.quote_id for q in quotes)


# ------------------------------------------------------------------------------------ math
def test_devig_edge_ev_clv_match_hand_computation():
    odds = (2.0, 4.0, 4.0)  # implied 0.5 + 0.25 + 0.25 = 1.0 -> no margin
    assert overround(odds) == pytest.approx(0.0) and devig(odds) == pytest.approx([0.5, 0.25, 0.25])
    odds = (1.8, 3.6, 5.0)
    imp = 1 / np.array(odds)
    assert overround(odds) == pytest.approx(imp.sum() - 1) and devig(odds).sum() == pytest.approx(1.0)
    p = (0.6, 0.25, 0.15)
    assert edge(p, odds) == pytest.approx(np.array(p) - imp / imp.sum())
    assert ev(p, odds) == pytest.approx([0.6 * 1.8 - 1, 0.25 * 3.6 - 1, 0.15 * 5.0 - 1])
    fair = 1 / devig(odds)[0]
    assert clv(fair, odds, 0) == pytest.approx(0.0)  # taking exactly the fair closing price: no CLV
    assert clv(fair * 1.1, odds, 0) == pytest.approx(0.1)
    with pytest.raises(ValueError):
        implied_probs((1.0, 3.0, 3.0))


def test_real_draftkings_prices_carry_a_positive_overround():
    odds = tuple(
        q.decimal_odds
        for q in sorted(
            (q for q in arsenal()["quotes"] if q.snapshot_type == "pre_match"),
            key=lambda q: "HDA".index(q.selection),
        )
    )
    assert overround(odds) > 0 and devig(odds).sum() == pytest.approx(1.0)


# ------------------------------------------------------------------------- timestamp gate
def kickoff():
    return arsenal()["kickoff_utc"]


def test_eligible_value_rows_for_real_forecasts_use_exact_quotes_after_the_forecast():
    quotes = arsenal()["quotes"]
    assert PREDS and all(p.generated_at < OBSERVED < kickoff() for p in PREDS)
    for p in PREDS:
        row = value_row(p, quotes, kickoff(), "espn-401879268")
        assert (
            row.status == "ELIGIBLE" and row.bookmaker == "DraftKings" and row.source_latency_known is False
        )
        probs = (p.p_home, p.p_draw, p.p_away)
        assert row.ev == pytest.approx(tuple(np.array(probs) * np.array(row.odds) - 1))
        assert row.edge == pytest.approx(tuple(np.array(probs) - np.array(row.market_probs_devig)))
        assert sum(row.market_probs_devig) == pytest.approx(1.0)


def test_non_exact_quotes_never_produce_numbers():
    unknown = [q.model_copy(update={"timestamp_quality": "unknown"}) for q in arsenal()["quotes"]]
    row = value_row(PREDS[0], unknown, kickoff(), "f")
    assert row.status == "NOT_ELIGIBLE" and "not exact" in row.reason
    assert row.ev is None and row.edge is None and row.odds is None  # no EV exposed


def test_quotes_must_follow_the_forecast_and_precede_kickoff():
    quotes = arsenal()["quotes"]
    late_pred = PREDS[0].model_copy(update={"generated_at": OBSERVED + timedelta(minutes=1)})
    assert "after the forecast" in value_row(late_pred, quotes, kickoff(), "f").reason
    assert value_row(PREDS[0], quotes, OBSERVED, "f").status == "NOT_ELIGIBLE"  # observed at/after kickoff
    assert "no complete 1X2" in value_row(PREDS[0], quotes[:2], kickoff(), "f").reason  # incomplete line


def test_closing_reference_is_the_last_exact_snapshot_before_kickoff():
    quotes = arsenal()["quotes"]
    assert closing_reference(quotes, kickoff()) == pytest.approx(
        tuple(
            next(q.decimal_odds for q in quotes if q.selection == s and q.snapshot_type == "pre_match")
            for s in "HDA"
        )
    )
    assert closing_reference(quotes, OBSERVED) is None  # nothing observed strictly before that instant


# --------------------------------------------------------------------------- paper ledger
def test_paper_ledger_is_immutable_settles_correctly_and_warns_on_small_samples(tmp_path):
    quotes = arsenal()["quotes"]
    row = value_row(PREDS[0], quotes, kickoff(), "espn-401879268")
    ledger = PaperLedger(tmp_path)
    assert ledger.place(row, kickoff(), min_edge=9.0, min_ev=9.0, stake=1.0) is None  # thresholds not met
    bet = ledger.place(row, kickoff(), min_edge=-1.0, min_ev=-1.0, stake=1.0)
    assert bet is not None and bet.selection in "HDA"
    assert ledger.place(row, kickoff(), -1.0, -1.0, 1.0) is None  # the first decision stands
    closing = closing_reference(quotes, kickoff())
    win = ledger.settle(bet, bet.selection, closing, OBSERVED + timedelta(days=9))
    assert win.won and win.profit_units == pytest.approx(bet.odds_taken - 1)
    assert win.clv == pytest.approx(bet.odds_taken * devig(closing)["HDA".index(bet.selection)] - 1)
    assert win.clv < 0  # the offered price includes the margin, so vs the fair close it is below par
    with pytest.raises(ValueError):
        ledger.settle(bet, bet.selection, closing, OBSERVED)  # cannot settle twice
    s = summarize(ledger.bets(), ledger.settlements())
    assert s["bets_settled"] == 1 and s["reliable"] is False and str(MIN_BETS_FOR_ROI) in s["note"]


def test_losing_paper_bet_loses_exactly_the_stake(tmp_path):
    row = value_row(PREDS[0], arsenal()["quotes"], kickoff(), "espn-401879268")
    ledger = PaperLedger(tmp_path)
    bet = ledger.place(row, kickoff(), -1.0, -1.0, 2.0)
    other = next(s for s in "HDA" if s != bet.selection)
    assert ledger.settle(bet, other, None, OBSERVED).profit_units == pytest.approx(-2.0)
    assert ledger.settlements()[0].clv is None  # no exact closing observation: CLV not invented
    with pytest.raises(ValueError):
        PaperLedger(tmp_path).settle(bet, "X", None, OBSERVED)


# ----------------------------------------------------------------- value CLI over stored real data
def _odds_root(tmp_path):
    """A project root holding REAL stored quotes (ESPN capture) and a locked S13-style stage whose
    predictions are the real LLM forecasts for the same fixture."""
    import shutil

    from src.odds.run import value  # noqa: F401  (import check)

    root = tmp_path / "proj"
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", root / "configs")
    ev_ = arsenal()
    store = OddsStore(root, ev_["fixture_id"])
    store.write_meta(
        {
            "fixture_id": ev_["fixture_id"],
            "league": "EPL",
            "kickoff_utc": ev_["kickoff_utc"].isoformat(),
            "home_id": "ENG_arsenal",
            "away_id": "ENG_leeds_united",
            "home_name": "Arsenal",
            "away_name": "Leeds United",
        }
    )
    store.add(ev_["quotes"])
    stage = root / "artifacts" / "snapshots" / "fdorg-560593" / "t-24h"
    stage.mkdir(parents=True)
    (stage / "snapshot.json").write_text(
        json.dumps(
            {
                "home_id": "ENG_arsenal",
                "away_id": "ENG_leeds_united",
                "kickoff_utc": ev_["kickoff_utc"].isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (stage / "LOCK.json").write_text(
        json.dumps({"generated_at": PREDS[0].generated_at.isoformat()}), encoding="utf-8"
    )
    (stage / "predictions.jsonl").write_text(
        (CAP / "forecast_arsenal_leeds_predictions.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return root


def test_value_report_joins_stored_quotes_with_locked_forecasts_and_records_paper_bets(tmp_path):
    from src.config import OddsConfig, config_dir_for, load_config
    from src.odds.run import value

    root = _odds_root(tmp_path)
    assert isinstance(load_config("odds", config_dir_for(root)), OddsConfig)
    lines = value(root, paper=False)
    assert len(lines) == len(PREDS) and all("edge=" in x and "latency unknown" in x for x in lines)
    assert PaperLedger(root).bets() == []  # report only: nothing recorded without --paper
    # paper thresholds are read from configs/odds.yaml (minimum allowed: 0)
    (root / "configs" / "odds.yaml").write_text(
        "min_edge: 0.0\nmin_ev: 0.0\nstake_units: 1.0\nleagues: [PL]\n", encoding="utf-8"
    )
    expected = 0
    for p in PREDS:  # independent expectation: best-EV selection with edge >= 0 and EV >= 0
        r = value_row(p, arsenal()["quotes"], kickoff(), "espn-401879268")
        i = max(range(3), key=lambda k: r.ev[k])
        expected += r.edge[i] >= 0 and r.ev[i] >= 0
    lines = value(root, paper=True)
    assert sum("PAPER BET" in x for x in lines) == expected == len(PaperLedger(root).bets())
    assert not any("PAPER BET" in x for x in value(root, paper=True))  # the first decision stands


def test_value_report_says_so_when_no_forecast_exists_yet(tmp_path):
    import shutil

    from src.odds.run import settle, value

    root = _odds_root(tmp_path)
    shutil.rmtree(root / "artifacts" / "snapshots")
    assert "no locked pre-match forecast yet" in value(root, paper=True)[0]
    assert '"bets_placed": 0' in settle(root)[-1]  # nothing open: no network needed


def test_odds_cli_value_and_settle_run_offline_over_stored_data(tmp_path, capsys):
    from src.odds import run as odds_run

    root = _odds_root(tmp_path)
    assert odds_run.main(["--root", str(root), "value"]) == 0
    assert "edge=" in capsys.readouterr().out
    assert odds_run.main(["--root", str(root), "settle"]) == 0
    assert '"bets_settled": 0' in capsys.readouterr().out
    (root / "configs" / "odds.yaml").write_text("min_edge: -1\n", encoding="utf-8")  # invalid config
    assert odds_run.main(["--root", str(root), "value"]) == 2  # reported, not a traceback
