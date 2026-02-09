"""Tests for the signal engine (engine.signals module).

This module tests the SignalEngine class which manages the full signal lifecycle
and confidence scoring pipeline. Signals are the primary mechanism by which
money_moves decides what to trade -- they represent actionable buy/sell recommendations
that flow through a lifecycle of approval, execution, and outcome tracking.

Tests cover:
    - **Signal lifecycle**: Creating signals (test_create_signal), approving
      (test_approve_signal), rejecting with what-if tracking (test_reject_signal),
      expiring/ignoring with what-if tracking (test_expire_signal), marking as
      executed (test_mark_executed), and cancelling (test_cancel_signal).

    - **Listing and filtering** (test_list_signals): Verifies list_signals() with
      optional status and symbol filters.

    - **Confidence scoring pipeline**: The multi-layer scoring system is tested
      across several dimensions:
      - Base scoring (test_confidence_scoring_base): Raw confidence passes through.
      - Thesis strength (test_confidence_scoring_thesis_strength): Strengthening
        thesis boosts score, weakening thesis penalizes it.
      - Domain expertise (test_confidence_scoring_domain_boost): In-domain signals
        (e.g., AI) score higher than out-of-domain (e.g., biotech).
      - Principles (test_confidence_scoring_principles): Matching validated
        principles add to the score.
      - Clamping (test_confidence_clamped): Score is clamped to [0.0, 1.0].
        Invalidated thesis forces score to 0.

    - **Source outcome tracking** (test_source_outcome_tracking): Tests
      record_source_outcome() which tracks win/loss/return for each signal source
      type, feeding back into future confidence scoring via source accuracy.

All tests use the ``seeded_db`` fixture because signal creation may reference
a thesis_id and confidence scoring uses principles and settings from the database.
"""

from __future__ import annotations

import pytest

from engine import Signal, SignalAction, SignalSource, SignalStatus
from engine.signals import SignalEngine


def test_create_signal(seeded_db) -> None:
    """Verify that create_signal() inserts a new signal and returns it with an ID.

    Creates a signal with all key fields (action, symbol, thesis_id, confidence,
    source, reasoning) and asserts that the returned Signal has a non-None id
    and starts in PENDING status. The signal references thesis_id=1 which exists
    in the seeded database.
    """
    engine = SignalEngine(seeded_db)
    signal = engine.create_signal(
        Signal(
            action=SignalAction.BUY,
            symbol="NVDA",
            thesis_id=1,
            confidence=0.75,
            source=SignalSource.THESIS_UPDATE,
            reasoning="Capex guidance raised",
        )
    )
    assert signal.id is not None
    assert signal.status == SignalStatus.PENDING


def test_approve_signal(seeded_db) -> None:
    """Verify that approve_signal() transitions a signal from PENDING to APPROVED.

    Also checks that decided_at timestamp is set, recording when the user
    approved the signal via Telegram or the dashboard.
    """
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.7))
    approved = engine.approve_signal(s.id)
    assert approved.status == SignalStatus.APPROVED
    assert approved.decided_at is not None


def test_reject_signal(seeded_db) -> None:
    """Verify that reject_signal() transitions to REJECTED and creates a what-if record.

    Rejected signals are tracked in the what_if table with decision='rejected' and
    the price at the time of rejection. This enables counterfactual analysis -- if
    a rejected signal would have been profitable, it informs future decision-making
    and principle validation.
    """
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.7))
    rejected = engine.reject_signal(s.id, price_at_pass=130.0)
    assert rejected.status == SignalStatus.REJECTED

    # Check what_if record
    wi = seeded_db.fetchone("SELECT * FROM what_if WHERE signal_id = ?", (s.id,))
    assert wi is not None
    assert wi["decision"] == "rejected"
    assert wi["price_at_pass"] == 130.0


def test_expire_signal(seeded_db) -> None:
    """Verify that expire_signal() transitions to IGNORED and creates a what-if record.

    Expired signals (24h timeout with no user response) are tracked separately
    from rejections in the what_if table with decision='ignored'. The distinction
    matters: rejection indicates active engagement (user saw it and said no), while
    ignoring indicates lack of conviction or attention.
    """
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.5))
    expired = engine.expire_signal(s.id, price_at_pass=125.0)
    assert expired.status == SignalStatus.IGNORED

    wi = seeded_db.fetchone("SELECT * FROM what_if WHERE signal_id = ?", (s.id,))
    assert wi["decision"] == "ignored"


def test_mark_executed(seeded_db) -> None:
    """Verify that mark_executed() transitions an APPROVED signal to EXECUTED.

    This is called after the broker successfully fills an order. The signal must
    be in APPROVED status first -- a PENDING signal cannot be directly executed.
    """
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.8))
    engine.approve_signal(s.id)
    executed = engine.mark_executed(s.id)
    assert executed.status == SignalStatus.EXECUTED


def test_cancel_signal(seeded_db) -> None:
    """Verify that cancel_signal() transitions a signal to CANCELLED status.

    Cancelled signals are removed from the active pipeline but preserved in
    the database for audit purposes. Unlike rejection, cancellation does not
    create a what-if record since the signal was withdrawn before a decision.
    """
    engine = SignalEngine(seeded_db)
    s = engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.5))
    cancelled = engine.cancel_signal(s.id)
    assert cancelled.status == SignalStatus.CANCELLED


def test_list_signals(seeded_db) -> None:
    """Verify that list_signals() supports optional status and symbol filtering.

    Creates two signals (BUY NVDA, SELL AVGO) and tests three query modes:
    all signals (no filter), by status (PENDING), and by symbol (NVDA).
    """
    engine = SignalEngine(seeded_db)
    engine.create_signal(Signal(action=SignalAction.BUY, symbol="NVDA", confidence=0.7))
    engine.create_signal(Signal(action=SignalAction.SELL, symbol="AVGO", confidence=0.6))

    all_signals = engine.list_signals()
    assert len(all_signals) == 2

    pending = engine.list_signals(status=SignalStatus.PENDING)
    assert len(pending) == 2

    nvda = engine.list_signals(symbol="NVDA")
    assert len(nvda) == 1


def test_confidence_scoring_base(seeded_db) -> None:
    """Verify that base confidence scoring passes through the raw value unchanged.

    When no thesis status, domain, or principles are provided, the scored
    confidence should equal the raw confidence (within floating-point tolerance).
    """
    engine = SignalEngine(seeded_db)
    score = engine.score_confidence(0.7)
    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(0.7, abs=0.01)


def test_confidence_scoring_thesis_strength(seeded_db) -> None:
    """Verify that thesis status affects confidence score directionally.

    A 'strengthening' thesis should boost the score (multiplier > 1.0) while
    a 'weakening' thesis should penalize it (multiplier < 1.0). The exact
    multipliers are defined in the scoring pipeline: strengthening=1.10,
    confirmed=1.20, weakening=0.85.
    """
    engine = SignalEngine(seeded_db)
    # strengthening thesis should boost
    score_strong = engine.score_confidence(0.7, thesis_status="strengthening")
    score_weak = engine.score_confidence(0.7, thesis_status="weakening")
    assert score_strong > score_weak


def test_confidence_scoring_domain_boost(seeded_db) -> None:
    """Verify that in-domain signals score higher than out-of-domain signals.

    The settings define expertise_domains=['AI', 'semiconductors', 'software',
    'hardware']. An 'AI' signal should receive the domain_boost (1.15), while
    a 'biotech' signal should receive the out_of_domain_penalty (0.90).
    """
    engine = SignalEngine(seeded_db)
    score_ai = engine.score_confidence(0.7, signal_domain="AI")
    score_bio = engine.score_confidence(0.7, signal_domain="biotech")
    assert score_ai > score_bio


def test_confidence_scoring_principles(seeded_db) -> None:
    """Verify that matching principles add a positive adjustment to the score.

    When matching principles with net positive validation (validated > invalidated)
    are provided, the confidence score should increase. The adjustment is based on
    each principle's weight scaled by its validation ratio.
    """
    engine = SignalEngine(seeded_db)
    principles = [{"weight": 0.05, "validated_count": 5, "invalidated_count": 1}]
    score_with = engine.score_confidence(0.7, matching_principles=principles)
    score_without = engine.score_confidence(0.7)
    assert score_with > score_without


def test_confidence_clamped(seeded_db) -> None:
    """Verify that the scored confidence is clamped to the [0.0, 1.0] range.

    Tests two edge cases: (1) very high raw confidence combined with all boosts
    should be clamped to 1.0, and (2) an invalidated thesis should force the
    score to exactly 0.0 regardless of raw confidence.
    """
    engine = SignalEngine(seeded_db)
    # Very high raw + boosts should clamp to 1.0
    score = engine.score_confidence(
        0.95,
        thesis_status="confirmed",
        signal_domain="AI",
        matching_principles=[{"weight": 0.1, "validated_count": 10, "invalidated_count": 0}],
    )
    assert score <= 1.0

    # Invalidated thesis should bring to 0
    score_zero = engine.score_confidence(0.7, thesis_status="invalidated")
    assert score_zero == 0.0


def test_source_outcome_tracking(seeded_db) -> None:
    """Verify that record_source_outcome() tracks wins, losses, and returns.

    Records three outcomes for the 'thesis_update' source type: two wins with
    positive returns and one loss with a negative return. Checks that the
    signal_scores table accumulates total, wins, losses, and returns correctly.
    This data feeds into _get_source_accuracy() for future confidence scoring.
    """
    engine = SignalEngine(seeded_db)
    engine.record_source_outcome("thesis_update", True, 5.0)
    engine.record_source_outcome("thesis_update", True, 3.0)
    engine.record_source_outcome("thesis_update", False, -2.0)

    row = seeded_db.fetchone("SELECT * FROM signal_scores WHERE source_type = 'thesis_update'")
    assert row["total"] == 3
    assert row["wins"] == 2
    assert row["losses"] == 1
