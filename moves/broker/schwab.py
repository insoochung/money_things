"""Schwab broker adapter using schwab-py for live trading.

Implements the Broker abstract interface from broker.base using the Charles Schwab
API via the schwab-py library (v1.5.0). Handles OAuth token management, order
placement, position synchronization, and error recovery.

This module is used in live mode only. All orders go through the Schwab API,
and positions are reconciled between the local database and Schwab's records.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from broker.base import Broker
from db.database import Database
from engine import (
    AccountBalance,
    ActorType,
    Order,
    OrderPreview,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Side,
    SignalAction,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


def _audit(db: Database, action: str, detail: str) -> None:
    """Create an audit log entry for a broker action."""
    now = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT INTO audit_log (actor, action, detail, timestamp) VALUES (?, ?, ?, ?)",
        (ActorType.ENGINE, action, detail, now),
    )


class SchwabBroker(Broker):
    """Live broker using Charles Schwab API via schwab-py.

    Connects to Schwab's trading API for real-money order execution,
    position retrieval, and account management. Handles OAuth token
    lifecycle and implements retry logic for transient failures.

    Attributes:
        db: Database instance for local position/order tracking.
        app_key: Schwab API application key.
        secret: Schwab API application secret.
        account_hash: Schwab account hash identifier.
        token_path: Path to the OAuth token JSON file.
        client: schwab-py client instance (lazy-initialized).
    """

    def __init__(
        self,
        db: Database,
        app_key: str,
        secret: str,
        account_hash: str,
        token_path: str = "config/schwab_token.json",
    ) -> None:
        """Initialize the Schwab broker.

        Args:
            db: Database instance for local state tracking.
            app_key: Schwab API application key.
            secret: Schwab API application secret.
            account_hash: Schwab account hash for the trading account.
            token_path: Path to store/read the OAuth token file.
        """
        self.db = db
        self.app_key = app_key
        self.secret = secret
        self.account_hash = account_hash
        self.token_path = token_path
        self.client: Any = None
        self._ensure_client()

    def _ensure_client(self) -> None:
        """Initialize the schwab-py client from token file."""
        if self.client is not None:
            return
        try:
            from schwab import auth

            self.client = auth.client_from_token_file(self.token_path, self.app_key, self.secret)
            logger.info("Schwab client initialized from token file")
        except Exception:
            logger.warning("Failed to initialize Schwab client from token file")
            self.client = None

    def refresh_token(self) -> bool:
        """Attempt to refresh the OAuth token.

        Returns:
            True if token was successfully refreshed, False otherwise.
        """
        try:
            from schwab import auth

            self.client = auth.client_from_token_file(self.token_path, self.app_key, self.secret)
            logger.info("Schwab token refreshed successfully")
            _audit(self.db, "token_refresh", "OAuth token refreshed")
            return True
        except Exception as e:
            logger.error("Failed to refresh Schwab token: %s", e)
            _audit(self.db, "token_refresh_failed", str(e))
            return False

    def _retry(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute a function with exponential backoff retry.

        Args:
            fn: Callable to execute.
            *args: Positional arguments for fn.
            **kwargs: Keyword arguments for fn.

        Returns:
            The return value of fn.

        Raises:
            Exception: If all retries are exhausted.
        """
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                self._ensure_client()
                if self.client is None:
                    msg = "Schwab client not initialized"
                    raise RuntimeError(msg)
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if "token" in err_str or "unauthorized" in err_str:
                    self.client = None
                    self.refresh_token()
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Schwab API retry %d/%d after %.1fs: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                    e,
                )
                time.sleep(delay)
        raise last_err  # type: ignore[misc]

    async def get_positions(self) -> list[Position]:
        """Retrieve positions from Schwab and sync to local DB.

        Returns:
            List of Position models from the Schwab account.
        """

        def _fetch() -> list[dict[str, Any]]:
            resp = self.client.get_account(
                self.account_hash,
                fields=[self.client.Account.Fields.POSITIONS],
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("securitiesAccount", {}).get("positions", [])

        try:
            raw_positions = self._retry(_fetch)
        except Exception as e:
            logger.error("Failed to get Schwab positions: %s", e)
            return []

        positions: list[Position] = []
        for pos in raw_positions:
            instrument = pos.get("instrument", {})
            symbol = instrument.get("symbol", "")
            qty = pos.get("longQuantity", 0) - pos.get("shortQuantity", 0)
            avg_price = pos.get("averagePrice", 0)
            side = Side.LONG if qty >= 0 else Side.SHORT
            positions.append(
                Position(
                    symbol=symbol,
                    shares=abs(qty),
                    avg_cost=avg_price,
                    side=side,
                )
            )
        return positions

    async def get_account_balance(self) -> AccountBalance:
        """Retrieve account balance from Schwab.

        Returns:
            AccountBalance with cash, total value, and buying power.
        """

        def _fetch() -> dict[str, Any]:
            resp = self.client.get_account(self.account_hash)
            resp.raise_for_status()
            return resp.json()

        try:
            data = self._retry(_fetch)
        except Exception as e:
            logger.error("Failed to get Schwab account info: %s", e)
            return AccountBalance()

        balances = data.get("securitiesAccount", {}).get("currentBalances", {})
        return AccountBalance(
            cash=balances.get("cashBalance", 0),
            total_value=balances.get("liquidationValue", 0),
            buying_power=balances.get("buyingPower", 0),
        )

    async def place_order(self, order: Order) -> OrderResult:
        """Submit an order to Schwab for execution.

        Args:
            order: Order model with symbol, action, shares, order_type, and
                optional limit_price.

        Returns:
            OrderResult with the Schwab order ID and initial status.
        """
        from schwab.orders.equities import (
            equity_buy_limit,
            equity_buy_market,
            equity_sell_limit,
            equity_sell_market,
        )

        def _place() -> Any:
            schwab_order = _build_schwab_order(order)
            return self.client.place_order(self.account_hash, schwab_order)

        def _build_schwab_order(o: Order) -> Any:
            is_buy = o.action in (SignalAction.BUY, SignalAction.COVER)
            shares = int(o.shares)
            if o.order_type == OrderType.LIMIT and o.limit_price is not None:
                if is_buy:
                    return equity_buy_limit(o.symbol, shares, o.limit_price)
                return equity_sell_limit(o.symbol, shares, o.limit_price)
            if is_buy:
                return equity_buy_market(o.symbol, shares)
            return equity_sell_market(o.symbol, shares)

        try:
            resp = self._retry(_place)
            resp.raise_for_status()
            # Extract order ID from Location header
            location = resp.headers.get("Location", "")
            schwab_order_id = location.split("/")[-1] if location else ""

            now = datetime.now(UTC).isoformat()
            _audit(
                self.db,
                "order_placed",
                f"{order.action} {int(order.shares)} {order.symbol} "
                f"via Schwab (id={schwab_order_id})",
            )

            # Record in orders table
            self.db.execute(
                """INSERT INTO orders (signal_id, order_type, symbol, action, shares,
                   limit_price, status, schwab_order_id, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.signal_id,
                    order.order_type,
                    order.symbol,
                    order.action,
                    order.shares,
                    order.limit_price,
                    OrderStatus.SUBMITTED,
                    schwab_order_id,
                    now,
                ),
            )

            return OrderResult(
                order_id=schwab_order_id,
                status=OrderStatus.SUBMITTED,
                message=(
                    f"Order submitted to Schwab: {order.action} {int(order.shares)} {order.symbol}"
                ),
            )
        except Exception as e:
            logger.error("Failed to place Schwab order: %s", e)
            _audit(self.db, "order_failed", f"{order.action} {order.symbol}: {e}")
            return OrderResult(
                order_id="",
                status=OrderStatus.REJECTED,
                message=f"Order rejected: {e}",
            )

    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Check current status of a Schwab order.

        Args:
            order_id: The Schwab-assigned order ID.

        Returns:
            Current OrderStatus for the order.
        """

        def _fetch() -> dict[str, Any]:
            resp = self.client.get_order(order_id, self.account_hash)
            resp.raise_for_status()
            return resp.json()

        try:
            data = self._retry(_fetch)
        except Exception as e:
            logger.error("Failed to get order status: %s", e)
            return OrderStatus.REJECTED

        status_map = {
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "PENDING_ACTIVATION": OrderStatus.PENDING,
            "QUEUED": OrderStatus.SUBMITTED,
            "WORKING": OrderStatus.SUBMITTED,
            "ACCEPTED": OrderStatus.SUBMITTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
        }
        schwab_status = data.get("status", "REJECTED")
        return status_map.get(schwab_status, OrderStatus.REJECTED)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending Schwab order.

        Args:
            order_id: The Schwab-assigned order ID to cancel.

        Returns:
            True if cancellation request was sent successfully.
        """

        def _cancel() -> Any:
            return self.client.cancel_order(order_id, self.account_hash)

        try:
            resp = self._retry(_cancel)
            resp.raise_for_status()
            now = datetime.now(UTC).isoformat()
            self.db.execute(
                "UPDATE orders SET status = ?, cancelled_at = ? WHERE schwab_order_id = ?",
                (OrderStatus.CANCELLED, now, order_id),
            )
            _audit(self.db, "order_cancelled", f"Schwab order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error("Failed to cancel Schwab order %s: %s", order_id, e)
            return False

    async def preview_order(self, order: Order) -> OrderPreview:
        """Estimate order cost using current market price.

        Args:
            order: Order model with symbol, action, shares.

        Returns:
            OrderPreview with estimated cost and price.
        """
        from engine.pricing import get_price

        price = get_price(order.symbol)
        warnings: list[str] = []
        if price is None:
            warnings.append(f"Price unavailable for {order.symbol}")
            return OrderPreview(warnings=warnings)

        estimated_cost = price * order.shares
        return OrderPreview(
            estimated_cost=estimated_cost,
            estimated_price=price,
            commission=0,
            warnings=warnings,
        )

    async def get_account_info(self) -> dict[str, Any]:
        """Retrieve full account information from Schwab.

        Returns:
            Raw account data dictionary from Schwab API.
        """

        def _fetch() -> dict[str, Any]:
            resp = self.client.get_account(
                self.account_hash,
                fields=[self.client.Account.Fields.POSITIONS],
            )
            resp.raise_for_status()
            return resp.json()

        try:
            return self._retry(_fetch)
        except Exception as e:
            logger.error("Failed to get account info: %s", e)
            return {"error": str(e)}

    def sync_positions(self) -> dict[str, Any]:
        """Reconcile DB positions with Schwab positions.

        Compares local database positions against Schwab's reported positions
        and returns any discrepancies found.

        Returns:
            Dictionary with 'synced', 'discrepancies', and 'schwab_only' lists.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                schwab_positions = pool.submit(
                    asyncio.run, self.get_positions()
                ).result()
        else:
            schwab_positions = asyncio.run(self.get_positions())

        db_rows = self.db.fetch_all(
            "SELECT symbol, shares, avg_cost FROM positions WHERE shares > 0"
        )
        db_map = {r["symbol"]: r for r in db_rows}

        result: dict[str, Any] = {
            "synced": [],
            "discrepancies": [],
            "schwab_only": [],
        }

        for pos in schwab_positions:
            if pos.symbol in db_map:
                db_pos = db_map.pop(pos.symbol)
                if abs(db_pos["shares"] - pos.shares) > 0.001:
                    result["discrepancies"].append(
                        {
                            "symbol": pos.symbol,
                            "db_shares": db_pos["shares"],
                            "schwab_shares": pos.shares,
                        }
                    )
                else:
                    result["synced"].append(pos.symbol)
            else:
                result["schwab_only"].append({"symbol": pos.symbol, "shares": pos.shares})

        for symbol, db_pos in db_map.items():
            result["discrepancies"].append(
                {
                    "symbol": symbol,
                    "db_shares": db_pos["shares"],
                    "schwab_shares": 0,
                }
            )

        _audit(
            self.db,
            "position_sync",
            f"Synced: {len(result['synced'])}, Discrepancies: {len(result['discrepancies'])}",
        )
        return result
