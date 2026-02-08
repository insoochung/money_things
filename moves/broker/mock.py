"""Mock broker: simulated fills at yfinance prices with FIFO lot accounting and cash management.

This module implements the MockBroker class, which provides a fully-functional simulated
broker for development, testing, and paper trading. It executes orders instantly at
the current yfinance price (with configurable slippage), manages positions and tax lots,
tracks cash balances, and records all trades.

The mock broker implements the full broker interface (broker.base.Broker) and provides
a realistic simulation of broker behavior:
    - BUY orders: Deduct cash, create new lot, update position (avg cost recalculated)
    - SELL orders: Add cash, consume lots FIFO (oldest first), calculate realized P/L,
      update position (delete if fully sold)
    - Cash checks: BUY/COVER orders rejected if insufficient cash
    - Price fetch: Uses engine.pricing.get_price() for current market prices
    - Slippage: Configurable basis-point slippage applied to fill prices
    - Audit trail: Every trade creates an audit_log entry

FIFO Lot Accounting:
    On SELL orders, lots are consumed in FIFO order (oldest acquired_date first).
    If a lot is fully consumed, it is closed (shares=0, closed_date and closed_price set).
    If only partially consumed, the lot's shares and cost_basis are reduced proportionally.
    Realized P/L is calculated as (sell_price - lot_cost_per_share) * shares_consumed
    for each lot consumed.

Cash Management:
    Cash is tracked in the portfolio_value table. BUY orders subtract from cash,
    SELL orders add to cash. Insufficient cash for a BUY order results in an
    OrderStatus.REJECTED result.

This module is used in:
    - Mock mode execution: All signal execution flows through MockBroker
    - Tests: Provides realistic broker behavior for integration tests
    - Paper trading: Can be used for forward-testing strategies without real money

Classes:
    MockBroker: Full broker implementation with instant fills and FIFO lot accounting.

Functions:
    _audit: Helper to create audit log entries for trade actions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from broker.base import Broker
from db.database import Database
from engine import (
    AccountBalance,
    ActorType,
    Order,
    OrderPreview,
    OrderResult,
    OrderStatus,
    Position,
    SignalAction,
)
from engine.pricing import get_price

logger = logging.getLogger(__name__)


class MockBroker(Broker):
    """Mock broker with instant fills at yfinance prices and FIFO lot accounting.

    Provides a complete broker simulation for development and testing. Orders are
    executed immediately at the current yfinance price (with optional slippage),
    positions and lots are maintained in the database, and cash is tracked.

    Attributes:
        db: Database instance for reading/writing positions, lots, trades, orders,
            portfolio_value, and audit_log tables.
        slippage_bps: Slippage in basis points (1 bp = 0.01%). Applied to the fill
            price: positive for buys (price increases), negative for sells (price
            decreases). Default is 0 (no slippage).
    """

    def __init__(self, db: Database, slippage_bps: float = 0) -> None:
        """Initialize the MockBroker.

        Args:
            db: Database instance for all persistence operations.
            slippage_bps: Slippage in basis points to apply to fill prices.
                Default 0 means fills at exact market price. A value of 10 means
                0.10% slippage (buy fills higher, sell fills lower).
        """
        self.db = db
        self.slippage_bps = slippage_bps

    async def get_positions(self) -> list[Position]:
        """Retrieve all open positions from the database.

        Queries the positions table for all rows with shares > 0 and converts
        them to Position models.

        Returns:
            List of Position models ordered by symbol. Empty list if no positions exist.
        """
        rows = self.db.fetchall("SELECT * FROM positions WHERE shares > 0 ORDER BY symbol")
        return [
            Position(
                id=r["id"],
                account_id=r.get("account_id"),
                symbol=r["symbol"],
                shares=r["shares"],
                avg_cost=r["avg_cost"],
                side=r.get("side", "long"),
                strategy=r.get("strategy", ""),
                thesis_id=r.get("thesis_id"),
            )
            for r in rows
        ]

    async def get_account_balance(self) -> AccountBalance:
        """Retrieve the current account balance from the most recent portfolio_value record.

        Returns:
            AccountBalance with cash, total_value, and buying_power (equal to cash
            in mock mode). Returns zero-valued AccountBalance if no portfolio data exists.
        """
        pv = self.db.fetchone("SELECT * FROM portfolio_value ORDER BY date DESC LIMIT 1")
        if pv:
            return AccountBalance(
                cash=pv.get("cash", 0),
                total_value=pv.get("total_value", 0),
                buying_power=pv.get("cash", 0),
            )
        return AccountBalance()

    async def place_order(self, order: Order) -> OrderResult:
        """Execute an order with instant fill at the current yfinance price.

        Processes the full order lifecycle in a single synchronous operation:
            1. Fetch current price from yfinance (via engine.pricing.get_price)
            2. Apply slippage to the fill price
            3. For BUY/COVER: check sufficient cash, reject if insufficient
            4. Record the order in the orders table with FILLED status
            5. For BUY: create a new lot and update/create the position
            6. For SELL: consume lots FIFO and update/delete the position
            7. Update cash in portfolio_value
            8. Record the trade in the trades table
            9. Create an audit log entry

        Args:
            order: Order model with symbol, action, shares, order_type, and optional
                signal_id and limit_price. The action determines the processing logic
                (BUY creates lots, SELL consumes them FIFO).

        Returns:
            OrderResult with:
                - FILLED status on success, with filled_price and filled_shares
                - REJECTED status on failure (price unavailable or insufficient cash),
                  with error message

        Side effects:
            - Network call to yfinance for current price (via get_price).
            - Inserts/updates rows in: orders, positions, lots, trades, portfolio_value,
              audit_log tables.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        symbol = order.symbol
        action = order.action
        shares = order.shares

        # Get current price
        price_data = get_price(symbol)
        if "error" in price_data:
            return OrderResult(
                order_id="0",
                status=OrderStatus.REJECTED,
                message=f"Price unavailable for {symbol}",
            )

        fill_price = price_data["price"]

        # Apply slippage
        if self.slippage_bps > 0:
            slippage = fill_price * (self.slippage_bps / 10000)
            if action in (SignalAction.BUY, SignalAction.COVER):
                fill_price += slippage
            else:
                fill_price -= slippage

        total_value = fill_price * shares

        # Check cash for buys
        if action in (SignalAction.BUY, SignalAction.COVER):
            balance = await self.get_account_balance()
            if total_value > balance.cash:
                return OrderResult(
                    order_id="0",
                    status=OrderStatus.REJECTED,
                    message=f"Insufficient cash: need ${total_value:.2f}, have ${balance.cash:.2f}",
                )

        # Record order
        cursor = self.db.execute(
            """INSERT INTO orders
               (signal_id, order_type, symbol, action, shares,
                limit_price, status, submitted_at, filled_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                order.signal_id,
                order.order_type.value,
                symbol,
                action.value,
                shares,
                order.limit_price,
                OrderStatus.FILLED.value,
                now,
                now,
            ),
        )
        order_id = str(cursor.lastrowid)

        # Process based on action
        realized_pnl = None
        lot_id = None

        if action == SignalAction.BUY:
            lot_id = self._create_lot(symbol, shares, fill_price, now, order.signal_id)
            self._update_position_buy(symbol, shares, fill_price)
            self._update_cash(-total_value)

        elif action == SignalAction.SELL:
            realized_pnl = self._consume_lots_fifo(symbol, shares, fill_price, now)
            self._update_position_sell(symbol, shares)
            self._update_cash(total_value)

        # Record trade
        self.db.execute(
            """INSERT INTO trades
               (signal_id, symbol, action, shares, price, total_value, lot_id,
                fees, broker, realized_pnl, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order.signal_id,
                symbol,
                action.value,
                shares,
                fill_price,
                total_value,
                lot_id,
                0,
                "mock",
                realized_pnl,
                now,
            ),
        )
        self.db.connect().commit()

        details = f"{action.value} {shares} {symbol} @ {fill_price}"
        _audit(self.db, "trade_executed", "trade", None, details)

        return OrderResult(
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_price=fill_price,
            filled_shares=shares,
            message=f"Filled {action.value} {shares} {symbol} @ ${fill_price:.2f}",
        )

    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Check the status of a previously submitted order.

        Args:
            order_id: String representation of the order's database ID.

        Returns:
            OrderStatus from the database, or REJECTED if the order is not found.
        """
        row = self.db.fetchone("SELECT status FROM orders WHERE id = ?", (int(order_id),))
        if row:
            return OrderStatus(row["status"])
        return OrderStatus.REJECTED

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Only affects orders in 'pending' status. Already-filled or cancelled orders
        are not modified.

        Args:
            order_id: String representation of the order's database ID to cancel.

        Returns:
            True (always returns True in mock mode).

        Side effects:
            - Updates the order's status to CANCELLED and sets cancelled_at timestamp
              for orders in 'pending' status.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE orders SET status = ?, cancelled_at = ? WHERE id = ? AND status = 'pending'",
            (OrderStatus.CANCELLED.value, now, int(order_id)),
        )
        self.db.connect().commit()
        return True

    async def preview_order(self, order: Order) -> OrderPreview:
        """Get a cost estimate for an order before submission.

        Fetches the current price and calculates the estimated cost. The mock
        broker charges no commission.

        Args:
            order: Order model with symbol and shares.

        Returns:
            OrderPreview with estimated_cost (price * shares), estimated_price,
            zero commission. Returns a warning if price is unavailable.

        Side effects:
            - Network call to yfinance for current price (via get_price).
        """
        price_data = get_price(order.symbol)
        if "error" in price_data:
            return OrderPreview(warnings=["Price unavailable"])

        price = price_data["price"]
        cost = price * order.shares
        return OrderPreview(
            estimated_cost=cost,
            estimated_price=price,
            commission=0,
        )

    def _create_lot(
        self, symbol: str, shares: float, price: float, date: str, signal_id: int | None
    ) -> int:
        """Create a new tax lot for a BUY order.

        Creates a new lot record in the lots table with the purchase details.
        The lot's position_id is looked up from the existing position for this
        symbol (if one exists).

        New lots are always created with holding_period='Short Term' since they
        are newly acquired. The holding period would be updated to 'Long Term'
        after 1 year (not yet implemented).

        Args:
            symbol: Ticker symbol purchased.
            shares: Number of shares in this lot.
            price: Fill price per share.
            date: ISO 8601 timestamp of the purchase.
            signal_id: ID of the signal that triggered this purchase (for tracing).

        Returns:
            The database ID of the newly created lot.

        Side effects:
            - Inserts a row into the lots table.
            - Commits the database transaction.
        """
        pos = self.db.fetchone("SELECT id, account_id FROM positions WHERE symbol = ?", (symbol,))
        position_id = pos["id"] if pos else None
        account_id = pos["account_id"] if pos else None

        cursor = self.db.execute(
            """INSERT INTO lots
               (position_id, account_id, symbol, shares, cost_basis,
                acquired_date, source, holding_period)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                position_id,
                account_id,
                symbol,
                shares,
                price * shares,
                date,
                "trade",
                "Short Term",
            ),
        )
        self.db.connect().commit()
        return cursor.lastrowid

    def _consume_lots_fifo(
        self, symbol: str, shares_to_sell: float, sell_price: float, date: str
    ) -> float:
        """Consume tax lots in FIFO order for a SELL order. Returns total realized P/L.

        Iterates through open lots for the symbol, ordered by acquired_date ascending
        (oldest first). For each lot:
            - If the lot has enough shares to cover the remaining sell, partially consume it
            - If the lot is fully consumed, close it (set closed_date, closed_price, shares=0)
            - Calculate realized P/L as (sell_price - cost_per_share) * shares_consumed

        Partial lot consumption reduces shares and cost_basis proportionally, preserving
        the cost-per-share ratio.

        Args:
            symbol: Ticker symbol being sold.
            shares_to_sell: Total number of shares to sell across lots.
            sell_price: Fill price per share.
            date: ISO 8601 timestamp of the sale.

        Returns:
            Total realized profit/loss across all consumed lots. Positive means profit,
            negative means loss.

        Side effects:
            - Updates lot records in the lots table (reduces shares/cost_basis or closes).
            - Commits the database transaction.
        """
        lots = self.db.fetchall(
            """SELECT * FROM lots
               WHERE symbol = ? AND closed_date IS NULL
               ORDER BY acquired_date ASC""",
            (symbol,),
        )

        remaining = shares_to_sell
        total_pnl = 0.0

        for lot in lots:
            if remaining <= 0:
                break

            lot_shares = lot["shares"]
            consume = min(lot_shares, remaining)
            cost_per_share = lot["cost_basis"] / lot_shares
            pnl = (sell_price - cost_per_share) * consume
            total_pnl += pnl

            if consume >= lot_shares:
                # Close entire lot
                self.db.execute(
                    "UPDATE lots SET closed_date = ?, closed_price = ?, shares = 0 WHERE id = ?",
                    (date, sell_price, lot["id"]),
                )
            else:
                # Partial close: reduce shares and cost_basis proportionally
                new_shares = lot_shares - consume
                new_cost = cost_per_share * new_shares
                self.db.execute(
                    "UPDATE lots SET shares = ?, cost_basis = ? WHERE id = ?",
                    (new_shares, new_cost, lot["id"]),
                )

            remaining -= consume

        self.db.connect().commit()
        return total_pnl

    def _update_position_buy(self, symbol: str, shares: float, price: float) -> None:
        """Update or create a position after a BUY order.

        If a position already exists for this symbol, recalculates the weighted
        average cost:
            new_avg = (old_avg * old_shares + price * new_shares) / total_shares

        If no position exists, creates a new one with the fill price as avg_cost.

        Args:
            symbol: Ticker symbol purchased.
            shares: Number of shares purchased.
            price: Fill price per share.

        Side effects:
            - Updates or inserts a row in the positions table.
            - Commits the database transaction.
        """
        pos = self.db.fetchone("SELECT * FROM positions WHERE symbol = ?", (symbol,))
        now = datetime.now(UTC).isoformat()

        if pos:
            old_shares = pos["shares"]
            old_cost = pos["avg_cost"]
            new_shares = old_shares + shares
            new_avg = ((old_cost * old_shares) + (price * shares)) / new_shares
            self.db.execute(
                "UPDATE positions SET shares = ?, avg_cost = ?, updated_at = ? WHERE id = ?",
                (new_shares, new_avg, now, pos["id"]),
            )
        else:
            self.db.execute(
                """INSERT INTO positions (symbol, shares, avg_cost, side, updated_at)
                   VALUES (?,?,?,'long',?)""",
                (symbol, shares, price, now),
            )
        self.db.connect().commit()

    def _update_position_sell(self, symbol: str, shares: float) -> None:
        """Update or delete a position after a SELL order.

        Reduces the position's share count. If the position reaches zero or
        negative shares, it is deleted from the positions table.

        Args:
            symbol: Ticker symbol sold.
            shares: Number of shares sold.

        Side effects:
            - Updates or deletes a row in the positions table.
            - Commits the database transaction.
        """
        pos = self.db.fetchone("SELECT * FROM positions WHERE symbol = ?", (symbol,))
        if pos:
            new_shares = pos["shares"] - shares
            now = datetime.now(UTC).isoformat()
            if new_shares <= 0:
                self.db.execute("DELETE FROM positions WHERE id = ?", (pos["id"],))
            else:
                self.db.execute(
                    "UPDATE positions SET shares = ?, updated_at = ? WHERE id = ?",
                    (new_shares, now, pos["id"]),
                )
            self.db.connect().commit()

    def _update_cash(self, amount: float) -> None:
        """Update the cash balance in the portfolio_value table.

        Adjusts the cash field of the most recent portfolio_value record by the
        given amount. Positive amount adds cash (from sells), negative amount
        subtracts cash (from buys).

        If no portfolio_value record exists, creates one with today's date.

        Args:
            amount: Dollar amount to add to cash. Positive for sells (cash inflow),
                negative for buys (cash outflow).

        Side effects:
            - Updates or inserts a row in the portfolio_value table.
            - Commits the database transaction.
        """
        pv = self.db.fetchone("SELECT * FROM portfolio_value ORDER BY date DESC LIMIT 1")
        if pv:
            new_cash = pv["cash"] + amount
            self.db.execute(
                "UPDATE portfolio_value SET cash = ? WHERE id = ?",
                (new_cash, pv["id"]),
            )
        else:
            self.db.execute(
                "INSERT INTO portfolio_value (date, cash) VALUES (date('now'), ?)",
                (max(0, amount),),
            )
        self.db.connect().commit()


def _audit(
    db: Database, action: str, entity_type: str, entity_id: int | None, details: str = ""
) -> None:
    """Create an audit log entry for a mock broker action.

    Records the action in the audit_log table with the ENGINE actor type.
    This provides a complete trail of all trade executions performed by
    the mock broker.

    Args:
        db: Database instance for writing the audit entry.
        action: The action performed (e.g., 'trade_executed').
        entity_type: The type of entity affected (e.g., 'trade').
        entity_id: The database ID of the affected entity (None for trades since
            the trade ID is not yet known at audit time).
        details: Additional context (e.g., 'BUY 10 NVDA @ 130.0').

    Side effects:
        - Inserts a row into the audit_log table.
        - Commits the database transaction.
    """
    db.execute(
        """INSERT INTO audit_log (actor, action, details, entity_type, entity_id)
           VALUES (?,?,?,?,?)""",
        (ActorType.ENGINE.value, action, details, entity_type, entity_id),
    )
    db.connect().commit()
