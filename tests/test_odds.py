"""S15 odds / edge / EV / CLV with the strict timestamp gate (ADR 0030, amended).

Real data: quotes come from a REAL ESPN (DraftKings) capture and forecasts are the REAL LLM
predictions for Arsenal v Leeds, generated before the capture. ESPN gives no quote timestamp, so those
quotes are `approximate` and the gate must refuse them. Positive-path gate tests need `exact` quotes,
which no keyless source provides: `exact_snapshot()` re-uses the REAL prices with an explicit
provider timestamp as a TEST PARAMETER of the gate (no provider claims exactness here)."""

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

REPO_ROOT = Path(__file__).resolve().parents[1]
CAP = Path(__file__).parent / "fixtures" / "real_provider_captures"
CAPTURE = json.loads((CAP / "espn_eng1_scoreboard.json").read_text(encoding="utf-8"))
OBSERVED = datetime.fromisoformat(CAPTURE["captured_at_utc"])
RAW = {e["id"]: e for e in CAPTURE["events"]}
PREDS = [
    PredictionRecord.from_json(x)
    for x in (CAP / "forecast_arsenal_leeds_predictions.jsonl").read_text(encoding="utf-8").splitlines()
    if x.strip()
]
LATENCY_S = 12.0  # test parameter: provider timestamp 12 s before we received the quote


def arsenal():
    return parse_event(RAW["401879268"], OBSERVED, "sha")


def kickoff():
    return arsenal()["kickoff_utc"]


def exact_snapshot(observed=OBSERVED, latency_s=LATENCY_S, source="gate-test"):
    """REAL ESPN prices carrying an explicit provider timestamp (gate-logic test input)."""
    out = []
    for q in arsenal()["quotes"]:
        if q.snapshot_type != "pre_match":
            continue
        d = json.loads(q.model_dump_json())
        d.update(
            source=source, observed_at=observed.isoformat(), timestamp_quality="exact",
            provider_timestamp=(observed - timedelta(seconds=latency_s)).isoformat(), source_latency_s=latency_s,
        )  # fmt: skip
        out.append(OddsQuote.model_validate(d))
    return out


# ----------------------------------------------------------------------------------- quotes
def test_american_to_decimal_known_values_and_invalid_input():
    assert american_to_decimal("+390") == pytest.approx(4.9)
    assert american_to_decimal("-260") == pytest.approx(1 + 100 / 260)
    assert american_to_decimal(650) == pytest.approx(7.5)
    for bad in ("abc", "0", "+50", "-50"):
        with pytest.raises(ValueError):
            american_to_decimal(bad)


def test_real_espn_quotes_are_approximate_never_exact():
    ev_ = arsenal()
    cur = [q for q in ev_["quotes"] if q.snapshot_type == "pre_match"]
    opn = [q for q in ev_["quotes"] if q.snapshot_type == "opening"]
    assert {q.selection for q in cur} == {"H", "D", "A"}
    assert all(q.timestamp_quality == "approximate" and q.provider_timestamp is None for q in cur)
    assert all(q.timestamp_quality == "unknown" for q in opn)  # the source gives no open time either
    assert all(q.bookmaker == "DraftKings" and q.source_latency_s is None for q in ev_["quotes"])
    draw = next(q for q in cur if q.selection == "D")
    assert draw.raw_price == "+390" and draw.decimal_odds == pytest.approx(4.9)


def test_exact_requires_a_provider_timestamp_and_a_measured_nonnegative_latency():
    base = json.loads(exact_snapshot()[0].model_dump_json())
    with pytest.raises(ValidationError):  # exact without a provider timestamp
        OddsQuote.model_validate({**base, "provider_timestamp": None})
    with pytest.raises(ValidationError):  # latency that is not observed_at - provider_timestamp
        OddsQuote.model_validate({**base, "source_latency_s": 1.0})
    future = (OBSERVED + timedelta(minutes=5)).isoformat()  # provider clock far ahead of ours
    with pytest.raises(ValidationError):
        OddsQuote.model_validate({**base, "provider_timestamp": future, "source_latency_s": -300.0})
    with pytest.raises(ValidationError):  # an opening price cannot be exact
        OddsQuote.model_validate({**base, "snapshot_type": "opening"})
    with pytest.raises(ValidationError):  # approximate quotes must not carry a provider time
        OddsQuote.model_validate({**base, "timestamp_quality": "approximate"})


def test_store_deduplicates_quotes_and_roundtrips(tmp_path):
    store = OddsStore(tmp_path, "espn-401879268")
    quotes = arsenal()["quotes"] + exact_snapshot()
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
    cur = sorted(
        (q for q in arsenal()["quotes"] if q.snapshot_type == "pre_match"),
        key=lambda q: "HDA".index(q.selection),
    )
    odds = tuple(q.decimal_odds for q in cur)
    assert overround(odds) > 0 and devig(odds).sum() == pytest.approx(1.0)


# ------------------------------------------------------------------------- timestamp gate
def test_real_approximate_quotes_give_no_edge_ev_or_clv_only_a_labelled_reference():
    assert PREDS and all(p.generated_at < OBSERVED < kickoff() for p in PREDS)
    for p in PREDS:
        row = value_row(p, arsenal()["quotes"], kickoff(), "espn-401879268")
        assert row.status == "NOT_ELIGIBLE" and "approximate" in row.reason and "no edge/EV/CLV" in row.reason
        assert row.edge is None and row.ev is None and row.odds is None and row.overround is None
        assert sum(row.reference_market_probs) == pytest.approx(1.0)  # shown only as a market reference


def test_eligible_value_rows_need_exact_quotes_observed_after_the_forecast():
    quotes = exact_snapshot()
    for p in PREDS:
        row = value_row(p, quotes, kickoff(), "espn-401879268")
        assert (
            row.status == "ELIGIBLE" and row.bookmaker == "DraftKings" and row.source_latency_s == LATENCY_S
        )
        probs = (p.p_home, p.p_draw, p.p_away)
        assert row.ev == pytest.approx(tuple(np.array(probs) * np.array(row.odds) - 1))
        assert row.edge == pytest.approx(tuple(np.array(probs) - np.array(row.market_probs_devig)))
        assert row.reference_market_probs is None and sum(row.market_probs_devig) == pytest.approx(1.0)


def test_a_snapshot_mixing_exact_and_approximate_quotes_is_refused():
    quotes = exact_snapshot()
    approx = [q for q in arsenal()["quotes"] if q.snapshot_type == "pre_match" and q.selection == "D"]
    mixed = [q for q in quotes if q.selection != "D"] + [
        q.model_copy(update={"observed_at": OBSERVED}) for q in approx
    ]
    row = value_row(PREDS[0], mixed, kickoff(), "f")
    assert row.status == "NOT_ELIGIBLE" and row.ev is None


def test_quotes_must_follow_the_forecast_and_precede_kickoff():
    quotes = exact_snapshot()
    late_pred = PREDS[0].model_copy(update={"generated_at": OBSERVED + timedelta(minutes=1)})
    assert "after the forecast" in value_row(late_pred, quotes, kickoff(), "f").reason
    assert value_row(PREDS[0], quotes, OBSERVED, "f").status == "NOT_ELIGIBLE"  # observed at/after kickoff
    assert "no complete 1X2" in value_row(PREDS[0], quotes[:2], kickoff(), "f").reason  # incomplete line


def test_closing_reference_is_the_last_exact_snapshot_before_kickoff():
    early = exact_snapshot(OBSERVED - timedelta(hours=1))
    late = exact_snapshot(OBSERVED)
    assert closing_reference(early + late, kickoff()) == pytest.approx(
        tuple(next(q.decimal_odds for q in late if q.selection == s) for s in "HDA")
    )
    assert closing_reference(early + late, OBSERVED) == pytest.approx(  # only the earlier one precedes it
        tuple(next(q.decimal_odds for q in early if q.selection == s) for s in "HDA")
    )
    assert (
        closing_reference(arsenal()["quotes"], kickoff()) is None
    )  # approximate quotes never serve as CLV close


# --------------------------------------------------------------------------- paper ledger
def eligible_row():
    return value_row(PREDS[0], exact_snapshot(), kickoff(), "espn-401879268")


def test_paper_ledger_is_immutable_settles_correctly_and_warns_on_small_samples(tmp_path):
    row = eligible_row()
    ledger = PaperLedger(tmp_path)
    assert ledger.place(row, kickoff(), min_edge=9.0, min_ev=9.0, stake=1.0) is None  # thresholds not met
    bet = ledger.place(row, kickoff(), min_edge=-1.0, min_ev=-1.0, stake=1.0)
    assert bet is not None and bet.selection in "HDA"
    assert ledger.place(row, kickoff(), -1.0, -1.0, 1.0) is None  # the first decision stands
    closing = closing_reference(exact_snapshot(), kickoff())
    win = ledger.settle(bet, bet.selection, closing, OBSERVED + timedelta(days=9))
    assert win.won and win.profit_units == pytest.approx(bet.odds_taken - 1)
    assert win.clv == pytest.approx(bet.odds_taken * devig(closing)["HDA".index(bet.selection)] - 1)
    assert win.clv < 0  # the offered price includes the margin, so vs the fair close it is below par
    with pytest.raises(ValueError):
        ledger.settle(bet, bet.selection, closing, OBSERVED)  # cannot settle twice
    s = summarize(ledger.bets(), ledger.settlements())
    assert s["bets_settled"] == 1 and s["reliable"] is False and str(MIN_BETS_FOR_ROI) in s["note"]


def test_losing_paper_bet_loses_exactly_the_stake(tmp_path):
    ledger = PaperLedger(tmp_path)
    bet = ledger.place(eligible_row(), kickoff(), -1.0, -1.0, 2.0)
    other = next(s for s in "HDA" if s != bet.selection)
    assert ledger.settle(bet, other, None, OBSERVED).profit_units == pytest.approx(-2.0)
    assert ledger.settlements()[0].clv is None  # no exact closing observation: CLV not invented
    with pytest.raises(ValueError):
        PaperLedger(tmp_path).settle(bet, "X", None, OBSERVED)


def test_no_paper_bet_can_ever_come_from_approximate_quotes(tmp_path):
    row = value_row(PREDS[0], arsenal()["quotes"], kickoff(), "espn-401879268")
    assert PaperLedger(tmp_path).place(row, kickoff(), -1.0, -1.0, 1.0) is None


# ----------------------------------------------------------------- value CLI over stored data
def _odds_root(tmp_path, exact: bool):
    """A project root holding stored quotes and a locked S13-style stage whose predictions are the real
    LLM forecasts for the same fixture. `exact=False`: the REAL ESPN (approximate) quotes."""
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", root / "configs")
    ev_ = arsenal()
    store = OddsStore(root, ev_["fixture_id"])
    store.write_meta(
        {
            "fixture_id": ev_["fixture_id"], "league": "EPL", "kickoff_utc": ev_["kickoff_utc"].isoformat(),
            "home_id": "ENG_arsenal", "away_id": "ENG_leeds_united", "home_name": "Arsenal",
            "away_name": "Leeds United",
        }
    )  # fmt: skip
    store.add(exact_snapshot() if exact else ev_["quotes"])
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


def test_value_cli_over_real_espn_quotes_reports_not_eligible_and_never_bets(tmp_path):
    from src.odds.run import value

    root = _odds_root(tmp_path, exact=False)
    lines = value(root, paper=True)
    assert len(lines) == len(PREDS) and all("NOT_ELIGIBLE" in x and "market reference" in x for x in lines)
    assert not any("edge=" in x for x in lines) and PaperLedger(root).bets() == []


def test_value_report_with_exact_quotes_joins_forecasts_and_records_paper_bets(tmp_path):
    from src.config import OddsConfig, config_dir_for, load_config
    from src.odds.run import value

    root = _odds_root(tmp_path, exact=True)
    assert isinstance(load_config("odds", config_dir_for(root)), OddsConfig)
    lines = value(root, paper=False)
    assert len(lines) == len(PREDS) and all("edge=" in x and "latency=12.0s" in x for x in lines)
    assert PaperLedger(root).bets() == []  # report only: nothing recorded without --paper
    (root / "configs" / "odds.yaml").write_text(
        "min_edge: 0.0\nmin_ev: 0.0\nstake_units: 1.0\nleagues: [PL]\n"
        "odds_api_reserve_credits: 0\nexact_horizon_hours: 26\nodds_api_bookmakers: [pinnacle]\n",
        encoding="utf-8",
    )
    expected = 0
    for p in PREDS:  # independent expectation: best-EV selection with edge >= 0 and EV >= 0
        r = value_row(p, exact_snapshot(), kickoff(), "espn-401879268")
        i = max(range(3), key=lambda k: r.ev[k])
        expected += r.edge[i] >= 0 and r.ev[i] >= 0
    lines = value(root, paper=True)
    assert sum("PAPER BET" in x for x in lines) == expected == len(PaperLedger(root).bets())
    assert not any("PAPER BET" in x for x in value(root, paper=True))  # the first decision stands


def test_value_report_says_so_when_no_forecast_exists_yet(tmp_path):
    import shutil

    from src.odds.run import settle, value

    root = _odds_root(tmp_path, exact=False)
    shutil.rmtree(root / "artifacts" / "snapshots")
    assert "no locked pre-match forecast yet" in value(root, paper=True)[0]
    assert '"bets_placed": 0' in settle(root)[-1]  # nothing open: no network needed


def test_odds_cli_value_and_settle_run_offline_over_stored_data(tmp_path, capsys):
    from src.odds import run as odds_run

    root = _odds_root(tmp_path, exact=True)
    assert odds_run.main(["--root", str(root), "value"]) == 0
    assert "edge=" in capsys.readouterr().out
    assert odds_run.main(["--root", str(root), "settle"]) == 0
    assert '"bets_settled": 0' in capsys.readouterr().out
    (root / "configs" / "odds.yaml").write_text("min_edge: -1\n", encoding="utf-8")  # invalid config
    assert odds_run.main(["--root", str(root), "value"]) == 2  # reported, not a traceback


def test_exact_odds_source_is_verified_and_collection_says_so_without_a_key(monkeypatch):
    from src.ingestion.interfaces import Capability, Support
    from src.odds.run import collect_exact
    from src.odds.theoddsapi import META

    assert META.capabilities[Capability.ODDS] == Support.SUPPORTED and META.verified_on == "2026-10-08"
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    assert "NOT_CONFIGURED" in collect_exact(REPO_ROOT, "PL")[0]
