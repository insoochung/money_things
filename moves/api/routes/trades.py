"""Trade history API endpoints.

This module provides REST API endpoints for retrieving executed trade history
with filtering and pagination capabilities. These endpoints power the dashboard
trade history table and trade analysis features.

Endpoints:
    GET /api/fund/trades - List executed trades with filtering options

Trade data includes execution details, realized P/L, and links back to
the original signals and theses that generated the trades.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses database to retrieve trade history and associated metadata
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class Trade(BaseModel):
    """Trade execution response model.

    Attributes:
        id: Trade database ID.
        signal_id: Associated signal ID.
        symbol: Stock symbol.
        action: Trade action (BUY, SELL, SHORT, COVER).
        shares: Number of shares traded.
        price: Execution price per share.
        total_value: Total trade value (shares * price).
        fees: Trading fees paid.
        broker: Broker used for execution.
        account_id: Account ID.
        realized_pnl: Realized profit/loss (for sells).
        timestamp: Execution timestamp.
        thesis_id: Associated thesis ID.
        thesis_title: Associated thesis title.
        signal_confidence: Original signal confidence.
        signal_source: Original signal source.
        lot_id: Lot ID (for sell trades).
        holding_period: Holding period in days (for sells).
        tax_impact: Tax impact category (short-term, long-term).
    """

    id: int = Field(..., description="Trade ID")
    signal_id: int | None = Field(None, description="Associated signal ID")
    symbol: str = Field(..., description="Stock symbol")
    action: str = Field(..., description="Trade action")
    shares: float = Field(..., description="Number of shares")
    price: float = Field(..., description="Execution price per share")
    total_value: float = Field(..., description="Total trade value")
    fees: float = Field(..., description="Trading fees")
    broker: str = Field(..., description="Broker used")
    account_id: int | None = Field(None, description="Account ID")
    realized_pnl: float | None = Field(None, description="Realized P/L")
    timestamp: str = Field(..., description="Execution timestamp")
    thesis_id: int | None = Field(None, description="Associated thesis ID")
    thesis_title: str | None = Field(None, description="Associated thesis title")
    signal_confidence: float | None = Field(None, description="Original signal confidence")
    signal_source: str | None = Field(None, description="Original signal source")
    lot_id: int | None = Field(None, description="Lot ID for sells")
    holding_period: int | None = Field(None, description="Holding period (days)")
    tax_impact: str | None = Field(None, description="Tax impact category")


class TradesSummary(BaseModel):
    """Summary statistics for trade results.

    Attributes:
        total_trades: Total number of trades.
        buy_trades: Number of buy trades.
        sell_trades: Number of sell trades.
        total_volume: Total trade volume in dollars.
        total_fees: Total fees paid.
        total_realized_pnl: Total realized P/L.
        win_rate: Percentage of profitable trades.
        avg_win: Average profit per winning trade.
        avg_loss: Average loss per losing trade.
        best_trade: Best trade P/L.
        worst_trade: Worst trade P/L.
    """

    total_trades: int = Field(..., description="Total number of trades")
    buy_trades: int = Field(..., description="Number of buy trades")
    sell_trades: int = Field(..., description="Number of sell trades")
    total_volume: float = Field(..., description="Total trade volume")
    total_fees: float = Field(..., description="Total fees paid")
    total_realized_pnl: float = Field(..., description="Total realized P/L")
    win_rate: float = Field(..., description="Win rate percentage")
    avg_win: float = Field(..., description="Average profit per win")
    avg_loss: float = Field(..., description="Average loss per loss")
    best_trade: float = Field(..., description="Best trade P/L")
    worst_trade: float = Field(..., description="Worst trade P/L")


@router.get("/trades", response_model=list[Trade])
async def list_trades(
    symbol: str | None = Query(None, description="Filter by symbol"),
    action: str | None = Query(None, description="Filter by action (BUY, SELL, SHORT, COVER)"),
    thesis_id: int | None = Query(None, description="Filter by thesis ID"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of trades to return"),
    offset: int = Query(0, ge=0, description="Number of trades to skip"),
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> list[Trade]:
    """List executed trades with optional filtering.

    Returns trade history ordered by execution time (newest first) with
    enriched data including signal information, thesis links, and P/L details.

    Args:
        symbol: Optional symbol filter.
        action: Optional action filter.
        thesis_id: Optional thesis filter.
        limit: Maximum number of trades to return.
        offset: Number of trades to skip for pagination.
        engines: Engine container with database.

    Returns:
        List of Trade models with execution details.
    """
    try:
        # Build WHERE clause based on filters
        where_conditions = []
        params = []

        if symbol:
            where_conditions.append("t.symbol = ?")
            params.append(symbol.upper())

        if action:
            where_conditions.append("t.action = ?")
            params.append(action.upper())

        if thesis_id:
            where_conditions.append("th.id = ?")
            params.append(thesis_id)

        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

        # Add limit and offset to params
        params.extend([limit, offset])

        # Query trades with joined signal and thesis data
        trades = engines.db.fetchall(
            f"""
            SELECT
                t.*,
                s.confidence as signal_confidence,
                s.source as signal_source,
                s.thesis_id,
                th.title as thesis_title,
                l.holding_period,
                CASE
                    WHEN l.holding_period IS NOT NULL AND l.holding_period >= 365
                    THEN 'long-term'
                    WHEN l.holding_period IS NOT NULL AND l.holding_period < 365
                    THEN 'short-term'
                    ELSE NULL
                END as tax_impact
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.id
            LEFT JOIN theses th ON s.thesis_id = th.id
            LEFT JOIN lots l ON t.lot_id = l.id
            {where_clause}
            ORDER BY t.timestamp DESC
            LIMIT ? OFFSET ?
        """,
            params,
        )

        result = []
        for trade in trades:
            result.append(
                Trade(
                    id=trade["id"],
                    signal_id=trade["signal_id"],
                    symbol=trade["symbol"],
                    action=trade["action"],
                    shares=trade["shares"],
                    price=trade["price"],
                    total_value=trade["total_value"],
                    fees=trade["fees"] or 0.0,
                    broker=trade["broker"],
                    account_id=trade["account_id"],
                    realized_pnl=trade["realized_pnl"],
                    timestamp=trade["timestamp"],
                    thesis_id=trade["thesis_id"],
                    thesis_title=trade["thesis_title"],
                    signal_confidence=trade["signal_confidence"],
                    signal_source=trade["signal_source"],
                    lot_id=trade["lot_id"],
                    holding_period=trade["holding_period"],
                    tax_impact=trade["tax_impact"],
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to list trades: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list trades: {str(e)}",
        )


@router.get("/trades/summary", response_model=TradesSummary)
async def get_trades_summary(
    symbol: str | None = Query(None, description="Filter by symbol"),
    thesis_id: int | None = Query(None, description="Filter by thesis ID"),
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> TradesSummary:
    """Get summary statistics for trade performance.

    Returns aggregated trade statistics for the specified time period
    and optional filters. Used for performance analysis and reporting.

    Args:
        symbol: Optional symbol filter.
        thesis_id: Optional thesis filter.
        days: Number of days to analyze (default 30).
        engines: Engine container with database.

    Returns:
        TradesSummary model with aggregated statistics.
    """
    try:
        # Build WHERE clause based on filters
        where_conditions = [f"t.timestamp >= datetime('now', '-{days} days')"]
        params = []

        if symbol:
            where_conditions.append("t.symbol = ?")
            params.append(symbol.upper())

        if thesis_id:
            where_conditions.append("s.thesis_id = ?")
            params.append(thesis_id)

        where_clause = "WHERE " + " AND ".join(where_conditions)

        # Get trade statistics
        stats = engines.db.fetchone(
            f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN t.action IN ('BUY', 'COVER') THEN 1 ELSE 0 END) as buy_trades,
                SUM(CASE WHEN t.action IN ('SELL', 'SHORT') THEN 1 ELSE 0 END) as sell_trades,
                SUM(ABS(t.total_value)) as total_volume,
                SUM(t.fees) as total_fees,
                SUM(t.realized_pnl) as total_realized_pnl,
                MAX(t.realized_pnl) as best_trade,
                MIN(t.realized_pnl) as worst_trade
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.id
            {where_clause}
        """,
            params,
        )

        if not stats:
            # Return empty summary if no trades found
            return TradesSummary(
                total_trades=0,
                buy_trades=0,
                sell_trades=0,
                total_volume=0.0,
                total_fees=0.0,
                total_realized_pnl=0.0,
                win_rate=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                best_trade=0.0,
                worst_trade=0.0,
            )

        # Calculate win rate and averages
        winning_trades = engines.db.fetchone(
            f"""
            SELECT
                COUNT(*) as win_count,
                AVG(t.realized_pnl) as avg_win
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.id
            {where_clause} AND t.realized_pnl > 0
        """,
            params,
        )

        losing_trades = engines.db.fetchone(
            f"""
            SELECT
                COUNT(*) as loss_count,
                AVG(t.realized_pnl) as avg_loss
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.id
            {where_clause} AND t.realized_pnl < 0
        """,
            params,
        )

        total_trades = stats["total_trades"] or 0
        win_count = winning_trades["win_count"] or 0
        losing_trades["loss_count"] or 0

        # Handle zero division cases
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
        avg_win = winning_trades["avg_win"] or 0.0
        avg_loss = losing_trades["avg_loss"] or 0.0

        return TradesSummary(
            total_trades=total_trades,
            buy_trades=stats["buy_trades"] or 0,
            sell_trades=stats["sell_trades"] or 0,
            total_volume=stats["total_volume"] or 0.0,
            total_fees=stats["total_fees"] or 0.0,
            total_realized_pnl=stats["total_realized_pnl"] or 0.0,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            best_trade=stats["best_trade"] or 0.0,
            worst_trade=stats["worst_trade"] or 0.0,
        )

    except Exception as e:
        logger.error("Failed to get trades summary: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get trades summary: {str(e)}",
        )
