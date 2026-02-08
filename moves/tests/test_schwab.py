"""Tests for the Schwab broker adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from broker.schwab import SchwabBroker
from engine import (
    Order,
    OrderStatus,
    OrderType,
    Side,
    SignalAction,
)


@pytest.fixture
def mock_db():
    """Create a mock database."""
    db = MagicMock()
    db.execute = MagicMock()
    db.fetch_one = MagicMock(return_value=None)
    db.fetch_all = MagicMock(return_value=[])
    return db


@pytest.fixture
def mock_client():
    """Create a mock schwab-py client."""
    client = MagicMock()
    client.Account.Fields.POSITIONS = "positions"
    return client


@pytest.fixture
def broker(mock_db, mock_client):
    """Create a SchwabBroker with mocked client."""
    with patch("broker.schwab.SchwabBroker._ensure_client"):
        b = SchwabBroker(
            db=mock_db,
            app_key="test_key",
            secret="test_secret",
            account_hash="test_hash",
        )
        b.client = mock_client
        return b


class TestGetPositions:
    """Tests for position retrieval."""

    def test_returns_positions(self, broker, mock_client):
        """Positions are parsed from Schwab API response."""
        resp = MagicMock()
        resp.json.return_value = {
            "securitiesAccount": {
                "positions": [
                    {
                        "instrument": {"symbol": "AAPL"},
                        "longQuantity": 100,
                        "shortQuantity": 0,
                        "averagePrice": 150.0,
                    },
                    {
                        "instrument": {"symbol": "TSLA"},
                        "longQuantity": 0,
                        "shortQuantity": 50,
                        "averagePrice": 200.0,
                    },
                ]
            }
        }
        resp.raise_for_status = MagicMock()
        mock_client.get_account.return_value = resp

        positions = asyncio.get_event_loop().run_until_complete(broker.get_positions())

        assert len(positions) == 2
        assert positions[0].symbol == "AAPL"
        assert positions[0].shares == 100
        assert positions[0].side == Side.LONG
        assert positions[1].symbol == "TSLA"
        assert positions[1].shares == 50
        assert positions[1].side == Side.SHORT

    def test_handles_api_error(self, broker, mock_client):
        """Returns empty list on API failure."""
        mock_client.get_account.side_effect = Exception("API down")

        positions = asyncio.get_event_loop().run_until_complete(broker.get_positions())
        assert positions == []


class TestGetAccountBalance:
    """Tests for account balance retrieval."""

    def test_returns_balance(self, broker, mock_client):
        """Balance is parsed from Schwab API response."""
        resp = MagicMock()
        resp.json.return_value = {
            "securitiesAccount": {
                "currentBalances": {
                    "cashBalance": 10000.0,
                    "liquidationValue": 50000.0,
                    "buyingPower": 20000.0,
                }
            }
        }
        resp.raise_for_status = MagicMock()
        mock_client.get_account.return_value = resp

        balance = asyncio.get_event_loop().run_until_complete(broker.get_account_balance())

        assert balance.cash == 10000.0
        assert balance.total_value == 50000.0
        assert balance.buying_power == 20000.0

    def test_handles_error(self, broker, mock_client):
        """Returns zero balance on API failure."""
        mock_client.get_account.side_effect = Exception("timeout")

        balance = asyncio.get_event_loop().run_until_complete(broker.get_account_balance())
        assert balance.cash == 0


class TestPlaceOrder:
    """Tests for order placement."""

    @patch("broker.schwab.equity_buy_market", create=True)
    def test_place_market_buy(self, mock_equity, broker, mock_client):
        """Market buy order is placed successfully."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.headers = {"Location": "https://api.schwab.com/orders/12345"}
        mock_client.place_order.return_value = resp

        order = Order(
            symbol="AAPL",
            action=SignalAction.BUY,
            shares=10,
            order_type=OrderType.MARKET,
        )

        with patch("schwab.orders.equities.equity_buy_market") as mock_builder:
            mock_builder.return_value = "mock_order_spec"
            result = asyncio.get_event_loop().run_until_complete(broker.place_order(order))

        assert result.status == OrderStatus.SUBMITTED
        assert result.order_id == "12345"

    def test_place_order_failure(self, broker, mock_client):
        """Returns rejected result on API failure."""
        mock_client.place_order.side_effect = Exception("Insufficient funds")

        order = Order(
            symbol="AAPL",
            action=SignalAction.BUY,
            shares=10,
        )

        with patch("schwab.orders.equities.equity_buy_market", create=True):
            result = asyncio.get_event_loop().run_until_complete(broker.place_order(order))

        assert result.status == OrderStatus.REJECTED
        assert "Insufficient funds" in result.message


class TestGetOrderStatus:
    """Tests for order status checking."""

    def test_filled_status(self, broker, mock_client):
        """FILLED status is correctly mapped."""
        resp = MagicMock()
        resp.json.return_value = {"status": "FILLED"}
        resp.raise_for_status = MagicMock()
        mock_client.get_order.return_value = resp

        status = asyncio.get_event_loop().run_until_complete(broker.get_order_status("123"))
        assert status == OrderStatus.FILLED

    def test_cancelled_status(self, broker, mock_client):
        """CANCELED status maps to CANCELLED."""
        resp = MagicMock()
        resp.json.return_value = {"status": "CANCELED"}
        resp.raise_for_status = MagicMock()
        mock_client.get_order.return_value = resp

        status = asyncio.get_event_loop().run_until_complete(broker.get_order_status("123"))
        assert status == OrderStatus.CANCELLED


class TestCancelOrder:
    """Tests for order cancellation."""

    def test_cancel_success(self, broker, mock_client, mock_db):
        """Successful cancellation updates DB."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        mock_client.cancel_order.return_value = resp

        result = asyncio.get_event_loop().run_until_complete(broker.cancel_order("123"))

        assert result is True
        mock_db.execute.assert_called()

    def test_cancel_failure(self, broker, mock_client):
        """Returns False on cancellation failure."""
        mock_client.cancel_order.side_effect = Exception("Not found")

        result = asyncio.get_event_loop().run_until_complete(broker.cancel_order("123"))
        assert result is False


class TestPreviewOrder:
    """Tests for order preview."""

    @patch("engine.pricing.get_price", return_value=150.0)
    def test_preview_with_price(self, mock_price, broker):
        """Preview returns estimated cost."""
        order = Order(symbol="AAPL", action=SignalAction.BUY, shares=10)

        preview = asyncio.get_event_loop().run_until_complete(broker.preview_order(order))

        assert preview.estimated_price == 150.0
        assert preview.estimated_cost == 1500.0

    @patch("engine.pricing.get_price", return_value=None)
    def test_preview_no_price(self, mock_price, broker):
        """Preview returns warning when price unavailable."""
        order = Order(symbol="FAKE", action=SignalAction.BUY, shares=10)

        preview = asyncio.get_event_loop().run_until_complete(broker.preview_order(order))

        assert len(preview.warnings) == 1


class TestRefreshToken:
    """Tests for token refresh."""

    @patch("schwab.auth.client_from_token_file")
    def test_refresh_success(self, mock_auth, broker, mock_db):
        """Successful token refresh updates client."""
        mock_auth.return_value = MagicMock()

        result = broker.refresh_token()

        assert result is True
        assert broker.client is not None

    @patch("schwab.auth.client_from_token_file", side_effect=Exception("expired"))
    def test_refresh_failure(self, mock_auth, broker):
        """Failed refresh returns False."""
        result = broker.refresh_token()
        assert result is False


class TestSyncPositions:
    """Tests for position sync."""

    def test_sync_detects_discrepancy(self, broker, mock_client, mock_db):
        """Sync detects share count mismatch."""
        resp = MagicMock()
        resp.json.return_value = {
            "securitiesAccount": {
                "positions": [
                    {
                        "instrument": {"symbol": "AAPL"},
                        "longQuantity": 100,
                        "shortQuantity": 0,
                        "averagePrice": 150.0,
                    }
                ]
            }
        }
        resp.raise_for_status = MagicMock()
        mock_client.get_account.return_value = resp

        mock_db.fetch_all.return_value = [{"symbol": "AAPL", "shares": 90, "avg_cost": 150.0}]

        result = broker.sync_positions()

        assert len(result["discrepancies"]) == 1
        assert result["discrepancies"][0]["db_shares"] == 90
        assert result["discrepancies"][0]["schwab_shares"] == 100
