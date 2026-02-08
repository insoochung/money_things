"""Tests for the risk management system (engine.risk module).

This module tests the RiskManager class which enforces 8 pre-trade risk checks
before any order can be executed. Risk management is a critical safety layer in
money_moves -- it prevents the system from taking positions that violate size
limits, exposure constraints, or drawdown thresholds.

Tests cover:
    - **Kill switch** (test_kill_switch_off, test_kill_switch_on,
      test_kill_switch_deactivate): The kill switch is an emergency halt mechanism
      that blocks ALL trading when active. Tests verify it passes when off, fails
      when on (with descriptive reason message), and can be toggled.

    - **Pre-trade check integration** (test_pre_trade_check_passes,
      test_pre_trade_fails_kill_switch): Tests the full pre_trade_check() method
      which runs all 8 risk checks in sequence. Verifies that a small, conservative
      signal passes all checks, and that an active kill switch fails the check.

    - **Exposure calculation** (test_exposure_calculation): Tests calculate_exposure()
      which computes long_value, short_value, gross_value, and net_value from
      current positions. This is the foundation for gross/net exposure checks.

    - **Drawdown calculation** (test_drawdown_calculation): Tests current_drawdown()
      which computes the decline from peak portfolio value. Uses two portfolio_value
      rows to simulate a 10% drawdown scenario.

    - **Trading window enforcement** (test_trading_window_meta,
      test_trading_window_meta_closed, test_non_meta_ignores_window): Tests the
      trading window check which blocks trading in restricted stocks during
      blackout periods. META (the user's employer) is the primary use case.
      Non-META symbols are not subject to window checks.

All tests use the ``seeded_db`` fixture which provides portfolio value ($100k total,
$50k cash), risk limits (defaults), and an inactive kill switch.
"""

from __future__ import annotations

from engine import Signal, SignalAction, SignalSource
from engine.risk import RiskManager


def test_kill_switch_off(seeded_db) -> None:
    """Verify that check_kill_switch() passes when the kill switch is inactive.

    The seeded database initializes the kill switch as inactive (active=FALSE).
    """
    rm = RiskManager(seeded_db)
    result = rm.check_kill_switch()
    assert result.passed


def test_kill_switch_on(seeded_db) -> None:
    """Verify that check_kill_switch() fails when the kill switch is active.

    Activates the kill switch with a reason and verifies that the risk check
    returns passed=False with 'Kill switch' in the reason string.
    """
    rm = RiskManager(seeded_db)
    rm.activate_kill_switch("test emergency", "user")
    result = rm.check_kill_switch()
    assert not result.passed
    assert "Kill switch" in result.reason


def test_kill_switch_deactivate(seeded_db) -> None:
    """Verify that the kill switch can be toggled off after activation.

    Tests the full activate -> verify active -> deactivate -> verify inactive cycle.
    """
    rm = RiskManager(seeded_db)
    rm.activate_kill_switch("test", "user")
    assert rm.is_kill_switch_active()
    rm.deactivate_kill_switch()
    assert not rm.is_kill_switch_active()


def test_pre_trade_check_passes(seeded_db) -> None:
    """Verify that pre_trade_check() passes for a conservative signal.

    Creates a small BUY signal (5% position size, well under the 15% limit)
    and runs it through all 8 risk checks. With the seeded database's $100k
    portfolio, $50k cash, no existing positions, and kill switch off, all
    checks should pass.
    """
    rm = RiskManager(seeded_db)
    signal = Signal(
        action=SignalAction.BUY,
        symbol="NVDA",
        confidence=0.7,
        source=SignalSource.MANUAL,
        size_pct=0.05,
    )
    result = rm.pre_trade_check(signal)
    assert result.passed


def test_pre_trade_fails_kill_switch(seeded_db) -> None:
    """Verify that pre_trade_check() fails immediately when kill switch is active.

    The kill switch check runs first in the pre-trade sequence. When active,
    it short-circuits all other checks and returns a failure with 'Kill switch'
    in the reason.
    """
    rm = RiskManager(seeded_db)
    rm.activate_kill_switch("emergency")
    signal = Signal(
        action=SignalAction.BUY,
        symbol="NVDA",
        confidence=0.7,
    )
    result = rm.pre_trade_check(signal)
    assert not result.passed
    assert "Kill switch" in result.reason


def test_exposure_calculation(seeded_db) -> None:
    """Verify that calculate_exposure() computes position values correctly.

    Inserts a single long position (100 shares of NVDA at $130 avg cost = $13,000)
    and verifies that long_value, short_value, gross_value, and net_value are all
    computed correctly. With only long positions: long=gross=net=$13,000, short=$0.
    """
    rm = RiskManager(seeded_db)
    # Add a position
    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 100, 130.0, 'long')"""
    )
    seeded_db.connect().commit()

    exposure = rm.calculate_exposure()
    assert exposure["long_value"] == 13000.0
    assert exposure["short_value"] == 0
    assert exposure["gross_value"] == 13000.0
    assert exposure["net_value"] == 13000.0


def test_drawdown_calculation(seeded_db) -> None:
    """Verify that current_drawdown() computes the decline from peak correctly.

    Sets up a scenario with peak NAV of $100,000 (from seeded data, dated
    earlier) and a current NAV of $90,000 (inserted with today's date). The
    expected drawdown is 10% ($10k decline from $100k peak).
    """
    rm = RiskManager(seeded_db)
    # Update existing row to have an earlier date at peak
    seeded_db.execute("UPDATE portfolio_value SET date = '2026-02-01'")
    # Add a later row showing decline
    seeded_db.execute(
        """INSERT INTO portfolio_value (date, total_value, cash)
           VALUES ('2026-02-07', 90000, 40000)"""
    )
    seeded_db.connect().commit()

    dd = rm.current_drawdown()
    assert dd > 0
    # 100k peak, 90k current = 10% drawdown
    assert abs(dd - 0.10) < 0.01


def test_trading_window_meta(seeded_db) -> None:
    """Verify that META signals pass during an open trading window.

    Inserts a wide trading window (2026-01-01 to 2026-12-31) for META and
    verifies that a BUY signal for META passes the trading window check.
    The check only applies to symbols that have entries in the trading_windows table.
    """
    rm = RiskManager(seeded_db)
    # Add a current trading window
    seeded_db.execute(
        """INSERT INTO trading_windows (symbol, opens, closes, notes)
           VALUES ('META', '2026-01-01', '2026-12-31', 'test window')"""
    )
    seeded_db.connect().commit()

    signal = Signal(
        action=SignalAction.BUY,
        symbol="META",
        confidence=0.7,
    )
    result = rm.check_trading_window(signal)
    assert result.passed


def test_trading_window_meta_closed(seeded_db) -> None:
    """Verify that META signals fail when the trading window has closed.

    Inserts an expired trading window (2025-01-01 to 2025-02-01) for META and
    verifies that a BUY signal for META fails with 'window' in the reason.
    This enforces employer trading restrictions during blackout periods.
    """
    rm = RiskManager(seeded_db)
    # Add a past trading window
    seeded_db.execute(
        """INSERT INTO trading_windows (symbol, opens, closes, notes)
           VALUES ('META', '2025-01-01', '2025-02-01', 'old window')"""
    )
    seeded_db.connect().commit()

    signal = Signal(
        action=SignalAction.BUY,
        symbol="META",
        confidence=0.7,
    )
    result = rm.check_trading_window(signal)
    assert not result.passed
    assert "window" in result.reason.lower()


def test_non_meta_ignores_window(seeded_db) -> None:
    """Verify that non-META symbols are not subject to trading window restrictions.

    NVDA has no trading windows in the database, so the check should pass
    automatically. Trading windows only apply to symbols that have explicit
    entries in the trading_windows table (currently only META).
    """
    rm = RiskManager(seeded_db)
    signal = Signal(
        action=SignalAction.BUY,
        symbol="NVDA",
        confidence=0.7,
    )
    result = rm.check_trading_window(signal)
    assert result.passed
