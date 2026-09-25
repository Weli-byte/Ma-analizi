import itertools

import pytest
from conftest import DV

from src.schemas import (
    InvalidTransition,
    LedgerConflict,
    PredictionLedger,
    PredictionRecord,
    PredictionStatus,
    can_transition,
    transition,
)

S = PredictionStatus
VALID = {
    (S.DRAFT, S.PUBLISHED),
    (S.DRAFT, S.VOID),
    (S.PUBLISHED, S.LOCKED),
    (S.PUBLISHED, S.VOID),
    (S.LOCKED, S.EVALUATED),
}


@pytest.mark.parametrize("old,new", list(itertools.product(S, S)))
def test_every_transition_pair(pred_kwargs, old, new):
    rec = PredictionRecord(**{**pred_kwargs, "status": old})
    if (old, new) in VALID:
        assert can_transition(old, new)
        out = transition(rec, new)
        assert out.status == new
        assert out.content_hash == rec.content_hash  # probabilities never change
        assert (out.p_home, out.p_draw, out.p_away) == (rec.p_home, rec.p_draw, rec.p_away)
    else:
        assert not can_transition(old, new)
        with pytest.raises(InvalidTransition):
            transition(rec, new)


def test_ledger_full_lifecycle_is_idempotent(pred_kwargs):
    ledger = PredictionLedger()
    rec = PredictionRecord(**pred_kwargs)
    assert ledger.append(rec) is True
    assert ledger.append(rec) is False  # exact re-append is a no-op
    for status in (S.PUBLISHED, S.LOCKED, S.EVALUATED):
        rec = transition(rec, status)
        assert ledger.append(rec) is True
    assert len(ledger) == 1 and ledger.latest(rec.logical_id).status == S.EVALUATED


def test_ledger_rejects_conflicting_content_for_same_logical_prediction(pred_kwargs):
    ledger = PredictionLedger()
    ledger.append(PredictionRecord(**pred_kwargs))
    changed = PredictionRecord(**{**pred_kwargs, "p_home": 0.6, "p_draw": 0.2})
    with pytest.raises(LedgerConflict):
        ledger.append(changed)
    # even after locking, probabilities cannot be rewritten
    locked = transition(transition(PredictionRecord(**pred_kwargs), S.PUBLISHED), S.LOCKED)
    ledger.append(transition(PredictionRecord(**pred_kwargs), S.PUBLISHED))
    ledger.append(locked)
    with pytest.raises(LedgerConflict):
        ledger.append(PredictionRecord(**{**pred_kwargs, "p_home": 0.7, "p_draw": 0.1, "status": "locked"}))


def test_ledger_rejects_skipped_and_initial_non_draft(pred_kwargs):
    ledger = PredictionLedger()
    with pytest.raises(InvalidTransition):
        ledger.append(PredictionRecord(**{**pred_kwargs, "status": "published"}))
    ledger.append(PredictionRecord(**pred_kwargs))
    with pytest.raises(InvalidTransition):
        ledger.append(PredictionRecord(**{**pred_kwargs, "status": "evaluated"}))  # skips states


def test_ledger_jsonl_persistence_roundtrip(tmp_path, pred_kwargs):
    path = tmp_path / "ledger.jsonl"
    a = PredictionLedger(path)
    rec = PredictionRecord(**pred_kwargs)
    a.append(rec)
    a.append(transition(rec, S.PUBLISHED))
    b = PredictionLedger(path)  # reload from disk enforces the same rules
    assert b.latest(rec.logical_id).status == S.PUBLISHED
    with pytest.raises(LedgerConflict):
        b.append(PredictionRecord(**{**pred_kwargs, "p_home": 0.4, "p_draw": 0.4, "p_away": 0.2}))
    assert DV in path.read_text()


def test_tampered_ledger_file_is_detected(tmp_path, pred_kwargs):
    path = tmp_path / "ledger.jsonl"
    PredictionLedger(path).append(PredictionRecord(**pred_kwargs))
    path.write_text(path.read_text().replace('"p_home":0.5', '"p_home":0.55'), encoding="utf-8")
    with pytest.raises(ValueError):  # probabilities no longer sum to 1 / hash mismatch
        PredictionLedger(path)
    good = PredictionRecord(**pred_kwargs).model_dump_json()
    bad = good.replace(good[good.index("prediction_id") + 16 : good.index("prediction_id") + 20], "abcd")
    with pytest.raises(ValueError, match="does not match"):
        PredictionRecord.from_json(bad)
