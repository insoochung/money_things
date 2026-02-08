"""Tests for the signal engine (engine.signals module)."""

from __future__ import annotations

import pytest

from engine import Signal, SignalAction, SignalSource, SignalStatus
from engine.signals import SignalEngine


def test_create_signal(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    signal = engine.create_signal(
        Signal(
            action=SignalAction.BUY,
            symbol="NVDA",
            thesis_id=1,
            confidence=0.75,
            source=SignalSource.THESIS_UPDATE,
            reasoning="Capex guidance raised",
        ),
        user_id=1,
    )
    assert signal.id is not None
    assert signal.status == SignalStatus.PENDING


def test_approve_signal(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.7), user_id=1)
    approved = engine.approve_signal(s.id, user_id=1)
    assert approved.status == SignalStatus.APPROVED
    assert approved.decided_at is not None


def test_reject_signal(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.7), user_id=1)
    rejected = engine.reject_signal(s.id, user_id=1, price_at_pass=130.0)
    assert rejected.status == SignalStatus.REJECTED

    wi = seeded_db.fetchone("SELECT * FROM what_if WHERE signal_id = ?", (s.id,))
    assert wi is not None
    assert wi["decision"] == "rejected"
    assert wi["price_at_pass"] == 130.0


def test_expire_signal(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.5), user_id=1)
    expired = engine.expire_signal(s.id, user_id=1, price_at_pass=125.0)
    assert expired.status == SignalStatus.IGNORED

    wi = seeded_db.fetchone("SELECT * FROM what_if WHERE signal_id = ?", (s.id,))
    assert wi["decision"] == "ignored"


def test_mark_executed(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.8), user_id=1)
    engine.approve_signal(s.id, user_id=1)
    executed = engine.mark_executed(s.id, user_id=1)
    assert executed.status == SignalStatus.EXECUTED


def test_cancel_signal(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.5), user_id=1)
    cancelled = engine.cancel_signal(s.id, user_id=1)
    assert cancelled.status == SignalStatus.CANCELLED


def test_list_signals(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.7), user_id=1)
    engine.create_signal(Signal(action=SignalAction.SELL, symbol="AVGO", confidence=0.6), user_id=1)

    all_signals = engine.list_signals(user_id=1)
    assert len(all_signals) == 2

    pending = engine.list_signals(user_id=1, status=SignalStatus.PENDING)
    assert len(pending) == 2

    nvda = engine.list_signals(user_id=1, symbol="NVDA")
    assert len(nvda) == 1


def test_confidence_scoring_base(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    score = engine.score_confidence(0.7)
    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(0.7, abs=0.01)


def test_confidence_scoring_thesis_strength(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    score_strong = engine.score_confidence(0.7, thesis_status="strengthening")
    score_weak = engine.score_confidence(0.7, thesis_status="weakening")
    assert score_strong > score_weak


def test_confidence_scoring_domain_boost(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    score_ai = engine.score_confidence(0.7, signal_domain="AI")
    score_bio = engine.score_confidence(0.7, signal_domain="biotech")
    assert score_ai > score_bio


def test_confidence_scoring_principles(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    principles = [{"weight": 0.05, "validated_count": 5, "invalidated_count": 1}]
    score_with = engine.score_confidence(0.7, matching_principles=principles)
    score_without = engine.score_confidence(0.7)
    assert score_with > score_without


def test_confidence_clamped(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    score = engine.score_confidence(
        0.95,
        thesis_status="confirmed",
        signal_domain="AI",
        matching_principles=[{"weight": 0.1, "validated_count": 10, "invalidated_count": 0}],
    )
    assert score <= 1.0

    score_zero = engine.score_confidence(0.7, thesis_status="invalidated")
    assert score_zero == 0.0


def test_source_outcome_tracking(seeded_db) -> None:
    engine = SignalEngine(seeded_db)
    engine.record_source_outcome("thesis_update", True, 5.0)
    engine.record_source_outcome("thesis_update", True, 3.0)
    engine.record_source_outcome("thesis_update", False, -2.0)

    row = seeded_db.fetchone("SELECT * FROM signal_scores WHERE source_type = 'thesis_update'")
    assert row["total"] == 3
    assert row["wins"] == 2
    assert row["losses"] == 1
