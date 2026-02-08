"""Tests for the mock broker (broker.mock module).

This module tests the MockBroker class which simulates order execution for
development, testing, and paper trading. The mock broker provides instant fills
at yfinance prices (mocked in tests) and implements the full Broker interface
including position management, account balance queries, lot-level FIFO accounting,
and trade recording.

All broker methods are async (matching the interface contract for the future live
Schwab broker), so tests use @pytest.mark.asyncio for async execution.

Price data is mocked via unittest.mock.patch to avoid real yfinance API calls.
The _mock_price() helper creates standardized price response dicts.

Tests cover:
    - **Position retrieval** (test_get_positions): Inserts a position and verifies
      get_positions() returns it with correct shares count.

    - **Account balance** (test_get_account_balance): Verifies get_account_balance()
      returns the seeded portfolio values ($50k cash, $100k total).

    - **Buy order execution** (test_place_buy_order): Places a market buy order and
      verifies: FILLED status, correct fill price and shares, cash decrease, and
      lot creation with correct shares count.

    - **Sell order with FIFO** (test_place_sell_order_fifo): Creates two lots with
      different acquisition dates and sells more shares than the first lot contains.
      Verifies FIFO consumption: first lot fully closed (shares=0, closed_date set),
      second lot partially consumed (remaining shares correct).

    - **Insufficient cash rejection** (test_insufficient_cash_rejected): Attempts to
      buy more than available cash allows and verifies REJECTED status with
      'Insufficient cash' message.

    - **Order preview** (test_preview_order): Tests preview_order() which returns
      estimated cost and commission without executing the order.

    - **Trade recording** (test_trade_recorded): Verifies that place_order() creates
      a trade record in the trades table with correct symbol, shares, price, and
      broker='mock'.

All tests use the ``seeded_db`` fixture which provides an account, portfolio value
($100k total, $50k cash), risk limits, and kill switch.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from broker.mock import MockBroker
from engine import Order, OrderStatus, OrderType, SignalAction


@pytest.fixture
def mock_broker(seeded_db):
    """Create a MockBroker instance backed by the seeded test database.

    The MockBroker wraps the Database for all persistence operations (positions,
    lots, trades, portfolio_value). The seeded database provides the initial
    account and portfolio state needed for order execution.
    """
    return MockBroker(seeded_db)


def _mock_price(symbol: str, price: float = 130.0):
    """Create a standardized mock price response dict.

    Returns a dict matching the structure of engine.pricing.get_price() output,
    used to patch the broker's price lookups in tests. Includes all fields that
    the MockBroker reads: symbol, price, change, change_percent, volume,
    timestamp, and source.

    Args:
        symbol: Ticker symbol for the price response.
        price: The mock price value. Defaults to 130.0.

    Returns:
        Dict with price data in the format expected by MockBroker.place_order().
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
async def test_get_positions(mock_broker, seeded_db) -> None:
    """Verify that get_positions() returns all positions with shares > 0.

    Inserts a position directly into the database and checks that get_positions()
    returns it as a Position model with the correct symbol and share count.
    """
    # Add a position
    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 50, 120.0, 'long')"""
    )
    seeded_db.connect().commit()

    positions = await mock_broker.get_positions()
    assert len(positions) >= 1
    nvda = [p for p in positions if p.symbol == "NVDA"]
    assert len(nvda) == 1
    assert nvda[0].shares == 50


@pytest.mark.asyncio
async def test_get_account_balance(mock_broker) -> None:
    """Verify that get_account_balance() returns seeded portfolio values.

    The seeded database has $50,000 cash and $100,000 total portfolio value.
    """
    balance = await mock_broker.get_account_balance()
    assert balance.cash == 50000
    assert balance.total_value == 100000


@pytest.mark.asyncio
async def test_place_buy_order(mock_broker, seeded_db) -> None:
    """Verify end-to-end buy order execution: fill, cash update, and lot creation.

    Places a market buy for 10 shares of NVDA at $130. Verifies:
    1. OrderResult has FILLED status with correct price and shares.
    2. Cash decreased from $50,000 to $48,700 ($50k - 10*$130).
    3. A lot was created for the new position with 10 shares.
    """
    with patch("broker.mock.get_price", return_value=_mock_price("NVDA", 130.0)):
        order = Order(
            symbol="NVDA",
            action=SignalAction.BUY,
            shares=10,
            order_type=OrderType.MARKET,
        )
        result = await mock_broker.place_order(order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_price == 130.0
        assert result.filled_shares == 10

    # Check cash decreased
    balance = await mock_broker.get_account_balance()
    assert balance.cash == 50000 - (130.0 * 10)

    # Check lot created
    lots = seeded_db.fetchall("SELECT * FROM lots WHERE symbol = 'NVDA'")
    assert len(lots) == 1
    assert lots[0]["shares"] == 10


@pytest.mark.asyncio
async def test_place_sell_order_fifo(mock_broker, seeded_db) -> None:
    """Verify FIFO lot consumption when selling across multiple lots.

    Sets up two lots for TEST: lot1 (20 shares, acquired 2025-01-01) and lot2
    (10 shares, acquired 2025-06-01). Sells 25 shares, which should consume
    all 20 from lot1 (oldest first, per FIFO) and 5 from lot2, leaving lot2
    with 5 remaining shares. Verifies lot1 is fully closed (shares=0,
    closed_date set) and lot2 is partially consumed (shares=5).
    """
    # Create two lots
    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('TEST', 30, 100.0, 'long')"""
    )
    seeded_db.connect().commit()
    pos = seeded_db.fetchone("SELECT id FROM positions WHERE symbol = 'TEST'")

    seeded_db.execute(
        """INSERT INTO lots (position_id, symbol, shares, cost_basis, acquired_date, source)
           VALUES (?, 'TEST', 20, 2000, '2025-01-01', 'trade')""",
        (pos["id"],),
    )
    seeded_db.execute(
        """INSERT INTO lots (position_id, symbol, shares, cost_basis, acquired_date, source)
           VALUES (?, 'TEST', 10, 1200, '2025-06-01', 'trade')""",
        (pos["id"],),
    )
    seeded_db.connect().commit()

    with patch("broker.mock.get_price", return_value=_mock_price("TEST", 110.0)):
        order = Order(
            symbol="TEST",
            action=SignalAction.SELL,
            shares=25,
            order_type=OrderType.MARKET,
        )
        result = await mock_broker.place_order(order)
        assert result.status == OrderStatus.FILLED

    # Check FIFO: first lot (20 shares) fully consumed, second lot partially (5 of 10)
    lots = seeded_db.fetchall("SELECT * FROM lots WHERE symbol = 'TEST' ORDER BY acquired_date")
    # First lot should be closed (shares=0)
    assert lots[0]["shares"] == 0
    assert lots[0]["closed_date"] is not None
    # Second lot should have 5 remaining
    assert lots[1]["shares"] == 5


@pytest.mark.asyncio
async def test_insufficient_cash_rejected(mock_broker) -> None:
    """Verify that buy orders exceeding available cash are rejected.

    Attempts to buy 1000 shares at $130 each ($130,000 total), which exceeds
    the $50,000 available cash. The order should be rejected with REJECTED
    status and 'Insufficient cash' in the message.
    """
    with patch("broker.mock.get_price", return_value=_mock_price("NVDA", 130.0)):
        order = Order(
            symbol="NVDA",
            action=SignalAction.BUY,
            shares=1000,  # 130k > 50k cash
            order_type=OrderType.MARKET,
        )
        result = await mock_broker.place_order(order)
        assert result.status == OrderStatus.REJECTED
        assert "Insufficient cash" in result.message


@pytest.mark.asyncio
async def test_preview_order(mock_broker) -> None:
    """Verify that preview_order() returns cost estimate without executing.

    Previews a buy order for 10 shares at $130. Should return estimated_cost
    of $1,300 and commission of $0 (mock broker has no commissions). The
    preview should NOT modify any database state.
    """
    with patch("broker.mock.get_price", return_value=_mock_price("NVDA", 130.0)):
        order = Order(
            symbol="NVDA",
            action=SignalAction.BUY,
            shares=10,
        )
        preview = await mock_broker.preview_order(order)
        assert preview.estimated_cost == 1300.0
        assert preview.commission == 0


@pytest.mark.asyncio
async def test_trade_recorded(mock_broker, seeded_db) -> None:
    """Verify that place_order() creates a trade record in the trades table.

    After executing a buy order, checks that the trades table contains a
    record with the correct symbol, shares, price, and broker='mock'.
    Trade records are the permanent audit trail of all executed orders.
    """
    with patch("broker.mock.get_price", return_value=_mock_price("NVDA", 130.0)):
        order = Order(
            symbol="NVDA",
            action=SignalAction.BUY,
            shares=5,
            order_type=OrderType.MARKET,
        )
        await mock_broker.place_order(order)

    trades = seeded_db.fetchall("SELECT * FROM trades WHERE symbol = 'NVDA'")
    assert len(trades) == 1
    assert trades[0]["shares"] == 5
    assert trades[0]["price"] == 130.0
    assert trades[0]["broker"] == "mock"
