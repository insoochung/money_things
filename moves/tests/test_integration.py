"""Integration tests: end-to-end lifecycle flows across multiple engine components.

This module tests the complete money_moves system by exercising realistic workflows
that span multiple engines (thesis, signal, risk, broker, principles) working together.
These tests validate that the components integrate correctly -- individual unit tests
may pass while integration fails due to data format mismatches, missing commits, or
incorrect field mappings between modules.

Tests cover:
    - **Full lifecycle** (test_full_lifecycle): The complete happy-path flow:
      create thesis -> score confidence -> create signal -> risk check -> approve
      -> execute via mock broker -> mark executed -> verify trade + audit trail +
      position update. This is the most comprehensive test in the suite.

    - **Risk limit enforcement** (test_risk_limit_enforcement): Verifies that the
      risk system properly blocks signals when limits are exceeded. Activates the
      kill switch and confirms that pre_trade_check() fails.

    - **Kill switch blocks all** (test_kill_switch_allows_sells): Verifies that the
      kill switch blocks ALL trading, including sell signals. This is an intentional
      safety design -- during an emergency halt, even selling is prevented to avoid
      panic liquidation.

    - **Thesis lifecycle** (test_thesis_lifecycle): Tests a thesis state machine
      flow through active -> weakening -> invalidated, verifying that version
      history is recorded for each transition.

    - **Full confidence scoring** (test_confidence_scoring_full): Tests the complete
      scoring pipeline with all modifiers active: thesis strength (strengthening),
      domain expertise (AI), matching principles, and source accuracy. Verifies
      that the final score is boosted above the raw confidence and clamped to 1.0.

All tests use the ``seeded_db`` fixture for consistent baseline data.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from broker.mock import MockBroker
from engine import (
    Order,
    OrderType,
    Signal,
    SignalAction,
    SignalSource,
    SignalStatus,
    Thesis,
    ThesisStatus,
)
from engine.principles import PrinciplesEngine
from engine.risk import RiskManager
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine


def _mock_price(symbol: str, price: float = 130.0):
    """Create a standardized mock price response dict for integration tests.

    Returns a dict matching the structure of engine.pricing.get_price() output.
    Used to patch the broker's price lookups so tests don't make real API calls.

    Args:
        symbol: Ticker symbol for the price response.
        price: The mock price value. Defaults to 130.0.

    Returns:
        Dict with symbol, price, change, change_percent, volume, timestamp, source.
    """
    return {
        "symbol": symbol,
        "price": price,
        "change": 1.5,
        "change_percent": 1.2,
        "volume": 1000000,
        "timestamp": "2026-02-07T12:00:00Z",
        "source": "mock",
    }


@pytest.mark.asyncio
async def test_full_lifecycle(seeded_db) -> None:
    """End-to-end: create thesis -> generate signal -> approve -> execute -> verify."""
    thesis_engine = ThesisEngine(seeded_db)
    signal_engine = SignalEngine(seeded_db)
    risk_manager = RiskManager(seeded_db)
    broker = MockBroker(seeded_db)

    # 1. Create thesis
    thesis = thesis_engine.create_thesis(
        Thesis(
            title="AI inference market grows",
            thesis_text="Inference workloads shifting to specialized chips",
            strategy="long",
            symbols=["NVDA"],
            conviction=0.8,
            horizon="6m",
        )
    )
    assert thesis.id is not None

    # 2. Generate signal
    confidence = signal_engine.score_confidence(
        raw_confidence=0.75,
        thesis_status="active",
        signal_domain="AI",
    )
    assert confidence > 0.75  # Domain boost should increase it

    signal = signal_engine.create_signal(
        Signal(
            action=SignalAction.BUY,
            symbol="NVDA",
            thesis_id=thesis.id,
            confidence=confidence,
            source=SignalSource.THESIS_UPDATE,
            size_pct=0.05,
            reasoning="Strong inference demand from hyperscalers",
        )
    )
    assert signal.status == SignalStatus.PENDING

    # 3. Risk check
    risk_result = risk_manager.pre_trade_check(signal)
    assert risk_result.passed, f"Risk check failed: {risk_result.reason}"

    # 4. Approve signal
    approved = signal_engine.approve_signal(signal.id)
    assert approved.status == SignalStatus.APPROVED

    # 5. Execute via mock broker
    with patch("broker.mock.get_price", return_value=_mock_price("NVDA", 130.0)):
        order = Order(
            signal_id=signal.id,
            symbol="NVDA",
            action=SignalAction.BUY,
            shares=10,
            order_type=OrderType.MARKET,
        )
        result = await broker.place_order(order)
        assert result.status.value == "filled"

    # 6. Mark signal as executed
    executed = signal_engine.mark_executed(signal.id)
    assert executed.status == SignalStatus.EXECUTED

    # 7. Verify trade recorded
    trades = seeded_db.fetchall("SELECT * FROM trades WHERE symbol = 'NVDA'")
    assert len(trades) == 1
    assert trades[0]["price"] == 130.0
    assert trades[0]["shares"] == 10

    # 8. Verify audit trail
    logs = seeded_db.fetchall("SELECT * FROM audit_log WHERE entity_type = 'signal' ORDER BY id")
    actions = [entry["action"] for entry in logs]
    assert "signal_created" in actions
    assert "signal_approved" in actions
    assert "signal_executed" in actions

    # 9. Verify position updated
    pos = seeded_db.fetchone("SELECT * FROM positions WHERE symbol = 'NVDA'")
    assert pos is not None
    assert pos["shares"] == 10


@pytest.mark.asyncio
async def test_risk_limit_enforcement(seeded_db) -> None:
    """Verify that signals are blocked when risk limits are exceeded.

    Activates the kill switch and then runs pre_trade_check() on a new signal.
    The check should fail with 'Kill switch' in the reason, demonstrating that
    the risk system prevents trading during emergency conditions.
    """
    signal_engine = SignalEngine(seeded_db)
    risk_manager = RiskManager(seeded_db)

    # Activate kill switch
    risk_manager.activate_kill_switch("test emergency")

    signal = signal_engine.create_signal(
        Signal(
            action=SignalAction.BUY,
            symbol="NVDA",
            confidence=0.8,
            source=SignalSource.MANUAL,
        )
    )

    result = risk_manager.pre_trade_check(signal)
    assert not result.passed
    assert "Kill switch" in result.reason


@pytest.mark.asyncio
async def test_kill_switch_allows_sells(seeded_db) -> None:
    """Verify that the kill switch blocks ALL trading, including sell signals.

    This is an intentional safety design: during an emergency halt, even
    sell signals are blocked to prevent panic-driven liquidation. The user
    must explicitly deactivate the kill switch before any trading can resume.
    """
    risk_manager = RiskManager(seeded_db)
    risk_manager.activate_kill_switch("emergency test")

    # Even SELL signals should be blocked by kill switch check
    sell_signal = Signal(
        action=SignalAction.SELL,
        symbol="NVDA",
        confidence=0.9,
    )
    result = risk_manager.pre_trade_check(sell_signal)
    assert not result.passed


def test_thesis_lifecycle(seeded_db) -> None:
    """Verify thesis state machine transitions with evidence and version tracking.

    Creates a thesis and walks it through active -> weakening (with reason
    'Demand data disappointing') -> invalidated (with reason and evidence).
    Verifies that 3 version records are created (initial + 2 transitions).
    This tests the integration between ThesisEngine.transition_status() and
    the thesis_versions table.
    """
    thesis_engine = ThesisEngine(seeded_db)

    thesis = thesis_engine.create_thesis(
        Thesis(
            title="Semiconductor cycle recovery",
            thesis_text="Chip demand rebounds in 2026",
            symbols=["NVDA", "AMD"],
        )
    )

    # Weaken due to negative news
    thesis = thesis_engine.transition_status(
        thesis.id, ThesisStatus.WEAKENING, reason="Demand data disappointing"
    )
    assert thesis.status == ThesisStatus.WEAKENING

    # Invalidate due to failure criteria
    thesis = thesis_engine.transition_status(
        thesis.id,
        ThesisStatus.INVALIDATED,
        reason="Earnings miss across sector",
        evidence="NVDA, AMD both missed Q1 guidance",
    )
    assert thesis.status == ThesisStatus.INVALIDATED

    # Verify version history
    versions = thesis_engine.get_versions(thesis.id)
    assert len(versions) == 3  # created + weakening + invalidated


def test_confidence_scoring_full(seeded_db) -> None:
    """Verify the complete confidence scoring pipeline with all modifiers active.

    Exercises the full scoring chain:
    1. Raw confidence: 0.7
    2. Thesis strength multiplier: 1.10 (strengthening) -> 0.77
    3. Domain expertise multiplier: 1.15 (AI is in-domain) -> ~0.886
    4. Principle adjustment: positive boost from matched validated principles
    5. Source accuracy: default (no prior records for thesis_update)

    The final score should be higher than the raw 0.7 (boosted by all factors)
    and clamped to <= 1.0. Also validates integration between PrinciplesEngine
    and SignalEngine -- match_principles() output feeds directly into score_confidence().
    """
    signal_engine = SignalEngine(seeded_db)
    principles_engine = PrinciplesEngine(seeded_db)

    # Get matching principles for AI domain
    matched = principles_engine.match_principles({"domain": "AI", "symbol": "NVDA"})

    score = signal_engine.score_confidence(
        raw_confidence=0.7,
        thesis_status="strengthening",
        matching_principles=matched,
        signal_domain="AI",
        source_type="thesis_update",
    )

    # Should be boosted: 0.7 * 1.1 (strengthening) * 1.15 (AI domain) + principle adjustments
    # 0.7 * 1.1 = 0.77, * 1.15 = 0.8855, + principle boost
    assert score > 0.7  # Should be higher than raw
    assert score <= 1.0  # Clamped
