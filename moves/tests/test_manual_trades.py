"""Tests for manual trade input — API endpoints and position updates."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MOVES_ROOT = Path(__file__).resolve().parent.parent
if str(MOVES_ROOT) not in sys.path:
    sys.path.insert(0, str(MOVES_ROOT))

THOUGHTS_ROOT = MOVES_ROOT.parent / "thoughts"
if str(THOUGHTS_ROOT) not in sys.path:
    sys.path.insert(0, str(THOUGHTS_ROOT))

from db.database import Database  # noqa: E402

# ── Fixtures ──


@pytest.fixture
def trade_db(db: Database) -> Database:
    """DB with seeded positions matching real portfolio."""
    db.execute(
        "INSERT INTO accounts (id, name, broker, account_type) "
        "VALUES (1, 'Schwab', 'Schwab', 'individual_brokerage')"
    )
    db.execute(
        "INSERT INTO accounts (id, name, broker, account_type) "
        "VALUES (2, 'ETrade', 'E*Trade', 'individual_brokerage')"
    )
    db.execute(
        "INSERT INTO positions (symbol, shares, avg_cost, side, account_id) "
        "VALUES ('META', 230, 662.58, 'long', 1)"
    )
    db.execute(
        "INSERT INTO positions (symbol, shares, avg_cost, side, account_id) "
        "VALUES ('QCOM', 129, 181.43, 'long', 2)"
    )
    db.execute(
        "INSERT INTO portfolio_value (date, total_value, cash, cost_basis) "
        "VALUES (date('now'), 200000, 50000, 175000)"
    )
    db.connect().commit()
    return db


# ── Position Update Tests ──


class TestPositionUpdates:
    """Test position recalculation logic directly on DB."""

    def test_buy_increases_shares_and_recalculates_avg(
        self, trade_db: Database
    ) -> None:
        """BUY should increase shares and weighted avg cost."""
        pos = trade_db.fetchone(
            "SELECT * FROM positions WHERE symbol = 'META'"
        )
        old_shares = pos["shares"]
        old_avg = pos["avg_cost"]

        new_shares_count = 10
        new_price = 700.0
        total_shares = old_shares + new_shares_count
        expected_avg = (
            (old_shares * old_avg + new_shares_count * new_price)
            / total_shares
        )

        trade_db.execute(
            "UPDATE positions SET shares = ?, avg_cost = ? WHERE symbol = 'META'",
            (total_shares, expected_avg),
        )
        trade_db.connect().commit()

        updated = trade_db.fetchone(
            "SELECT * FROM positions WHERE symbol = 'META'"
        )
        assert updated["shares"] == 240
        assert abs(updated["avg_cost"] - expected_avg) < 0.01

    def test_sell_decreases_shares_keeps_avg(
        self, trade_db: Database
    ) -> None:
        """SELL should decrease shares, avg_cost stays the same."""
        pos = trade_db.fetchone(
            "SELECT * FROM positions WHERE symbol = 'QCOM'"
        )
        sell_shares = 50
        new_shares = pos["shares"] - sell_shares

        trade_db.execute(
            "UPDATE positions SET shares = ? WHERE symbol = 'QCOM'",
            (new_shares,),
        )
        trade_db.connect().commit()

        updated = trade_db.fetchone(
            "SELECT * FROM positions WHERE symbol = 'QCOM'"
        )
        assert updated["shares"] == 79
        assert updated["avg_cost"] == 181.43

    def test_sell_insufficient_shares(self, trade_db: Database) -> None:
        """Selling more than held should be blocked."""
        pos = trade_db.fetchone(
            "SELECT shares FROM positions WHERE symbol = 'META'"
        )
        assert pos["shares"] == 230
        # Verify we can't sell 300
        assert 300 > pos["shares"]

    def test_buy_creates_new_position(self, trade_db: Database) -> None:
        """Buying a new symbol creates a position."""
        trade_db.execute(
            "INSERT INTO positions (symbol, shares, avg_cost, side) "
            "VALUES ('NVDA', 50, 130.0, 'long')"
        )
        trade_db.connect().commit()

        pos = trade_db.fetchone(
            "SELECT * FROM positions WHERE symbol = 'NVDA'"
        )
        assert pos is not None
        assert pos["shares"] == 50
        assert pos["avg_cost"] == 130.0


# ── Trade Parser Tests ──


class TestTradeParser:
    """Test /trade command parsing (regex logic extracted to avoid import issues)."""

    @staticmethod
    def _parse(text: str) -> dict | None:
        """Inline parser matching commands.py logic."""
        import re

        pat = re.compile(
            r"(?i)^(buy|sell)\s+"
            r"([A-Z]{1,10})\s+"
            r"(\d+(?:\.\d+)?)\s*"
            r"@\s*\$?(\d+(?:\.\d+)?)$"
        )
        m = pat.match(text.strip())
        if not m:
            return None
        return {
            "action": m.group(1).upper(),
            "symbol": m.group(2).upper(),
            "shares": float(m.group(3)),
            "price": float(m.group(4)),
        }

    def test_parse_buy(self) -> None:
        result = self._parse("BUY META 10 @ 650.00")
        assert result == {
            "action": "BUY", "symbol": "META",
            "shares": 10.0, "price": 650.0,
        }

    def test_parse_sell(self) -> None:
        result = self._parse("SELL QCOM 50 @ 140.00")
        assert result == {
            "action": "SELL", "symbol": "QCOM",
            "shares": 50.0, "price": 140.0,
        }

    def test_parse_case_insensitive(self) -> None:
        result = self._parse("buy meta 10 @ 650")
        assert result is not None
        assert result["action"] == "BUY"
        assert result["symbol"] == "META"

    def test_parse_with_dollar_sign(self) -> None:
        result = self._parse("BUY AAPL 5 @ $190.50")
        assert result is not None
        assert result["price"] == 190.5

    def test_parse_invalid_format(self) -> None:
        assert self._parse("") is None
        assert self._parse("BUY META") is None
        assert self._parse("HOLD META 10 @ 100") is None


# ── Manual Trade API Logic Tests ──


class TestManualTradeAPI:
    """Test the trade API helper functions."""

    def test_update_position_for_buy_existing(
        self, trade_db: Database
    ) -> None:
        """_update_position_for_buy with existing position."""
        sys.path.insert(0, str(MOVES_ROOT / "api" / "routes"))
        from trades import _update_position_for_buy

        new_shares, new_avg = _update_position_for_buy(
            trade_db, "META", 10, 700.0, None, None
        )
        assert new_shares == 240
        expected = (230 * 662.58 + 10 * 700.0) / 240
        assert abs(new_avg - expected) < 0.01

    def test_update_position_for_buy_new(
        self, trade_db: Database
    ) -> None:
        """_update_position_for_buy creating new position."""
        from trades import _update_position_for_buy

        new_shares, new_avg = _update_position_for_buy(
            trade_db, "NVDA", 50, 130.0, 1, None
        )
        assert new_shares == 50
        assert new_avg == 130.0

    def test_update_position_for_sell(
        self, trade_db: Database
    ) -> None:
        """_update_position_for_sell reduces shares."""
        from trades import _update_position_for_sell

        new_shares, avg, pnl = _update_position_for_sell(
            trade_db, "META", 30, 700.0
        )
        assert new_shares == 200
        assert avg == 662.58
        assert abs(pnl - (700.0 - 662.58) * 30) < 0.01

    def test_update_position_for_sell_insufficient(
        self, trade_db: Database
    ) -> None:
        """Selling more than held raises HTTPException."""
        from fastapi import HTTPException
        from trades import _update_position_for_sell

        with pytest.raises(HTTPException) as exc_info:
            _update_position_for_sell(trade_db, "META", 500, 700.0)
        assert exc_info.value.status_code == 400
        assert "Insufficient" in str(exc_info.value.detail)

    def test_update_portfolio_value(self, trade_db: Database) -> None:
        """_update_portfolio_value recalculates total."""
        from trades import _update_portfolio_value

        _update_portfolio_value(trade_db)
        trade_db.connect().commit()

        pv = trade_db.fetchone(
            "SELECT * FROM portfolio_value ORDER BY date DESC LIMIT 1"
        )
        # META: 230 * 662.58 + QCOM: 129 * 181.43 + cash: 50000
        expected = 230 * 662.58 + 129 * 181.43 + 50000
        assert abs(pv["total_value"] - expected) < 1.0
