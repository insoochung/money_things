"""Abstract broker interface for the money_moves execution engine.

This module defines the abstract base class (Broker) that all broker implementations
must conform to. The broker interface provides a consistent API for order management,
position retrieval, and account balance queries, regardless of the underlying broker
(mock simulation or live Schwab API).

The broker abstraction enables the money_moves system to run in two modes:
    - Mock mode: Uses MockBroker (broker.mock) with instant fills at yfinance prices
      for development, testing, and paper trading.
    - Live mode: Will use a SchwabBroker implementation with real money execution
      via the Schwab API (schwab-py library).

All broker methods are async to support non-blocking I/O for the live broker,
which needs to make HTTP calls to the Schwab API. The mock broker uses async
methods for interface compatibility even though its operations are synchronous.

The broker interface is used by:
    - The signal execution pipeline: After a signal is approved, an Order is created
      and passed to broker.place_order() for execution.
    - The dashboard API: Calls broker.get_positions() and broker.get_account_balance()
      to display current portfolio state.
    - The risk manager: Uses position data for exposure calculations.

Classes:
    Broker: Abstract base class defining the broker interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from engine import AccountBalance, Order, OrderPreview, OrderResult, OrderStatus, Position


class Broker(ABC):
    """Abstract base class for broker implementations.

    Defines the interface contract that all broker implementations must fulfill.
    Subclasses must implement all abstract methods. Currently two implementations
    are planned:
        - MockBroker (broker.mock): Simulated fills for development/testing
        - SchwabBroker (future): Live execution via Schwab API

    All methods are async to support non-blocking I/O in the live broker.
    """

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Retrieve all current positions with shares > 0.

        Returns:
            List of Position models representing all open positions, ordered by symbol.
            Returns an empty list if no positions exist.
        """
        ...

    @abstractmethod
    async def get_account_balance(self) -> AccountBalance:
        """Retrieve the current account balance and buying power.

        Returns:
            AccountBalance model with cash, total_value, and buying_power fields.
            Returns a zero-valued AccountBalance if no portfolio data exists.
        """
        ...

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult:
        """Submit an order for execution.

        Processes the order through the broker's execution logic. For the mock broker,
        this is an instant fill at the current yfinance price. For the live broker,
        this submits the order to Schwab's API.

        Args:
            order: Order model with symbol, action, shares, order_type, and optional
                limit_price and signal_id.

        Returns:
            OrderResult with the fill details (order_id, status, filled_price,
            filled_shares, message). Status will be FILLED on success or REJECTED
            on failure (insufficient cash, price unavailable, etc.).

        Side effects:
            - Creates/updates positions and lots in the database.
            - Records the trade in the trades table.
            - Updates cash in portfolio_value.
            - Creates audit log entries.
        """
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Check the current status of a previously submitted order.

        Args:
            order_id: The broker-assigned order ID (string representation of the
                database row ID for mock broker).

        Returns:
            Current OrderStatus for the order. Returns REJECTED if the order
            is not found.
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Only orders in PENDING status can be cancelled. Already-filled or
        already-cancelled orders are not affected.

        Args:
            order_id: The broker-assigned order ID to cancel.

        Returns:
            True if the cancellation was processed (even if the order was not in
            a cancellable state), False on error.

        Side effects:
            - Updates the order status to CANCELLED in the orders table.
            - Sets the cancelled_at timestamp.
        """
        ...

    @abstractmethod
    async def preview_order(self, order: Order) -> OrderPreview:
        """Get a cost estimate for an order before submission.

        Used to show the user the expected cost, price, and any warnings before
        they approve a signal in Telegram or the dashboard.

        Args:
            order: Order model with symbol, action, shares, and order_type.

        Returns:
            OrderPreview with estimated_cost, estimated_price, commission, and
            any warning messages.
        """
        ...
