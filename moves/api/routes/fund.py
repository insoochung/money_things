"""Fund portfolio API endpoints.

This module provides REST API endpoints for retrieving basic portfolio information
including fund status, positions, exposure, and NAV. These are the core endpoints
that power the dashboard's summary cards and position tables.

Endpoints:
    GET /api/fund/status - Overall fund status and NAV
    GET /api/fund/positions - All open positions with current values
    GET /api/fund/position/{ticker} - Individual position details
    GET /api/fund/exposure - Portfolio exposure breakdown

All endpoints return Pydantic models for type safety and automatic OpenAPI
documentation. The data is sourced from the database and enriched with
real-time pricing information from the pricing service.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses engine container for database and pricing service access
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class FundStatus(BaseModel):
    """Fund overview status response model.

    Attributes:
        nav: Current net asset value in dollars.
        total_return_pct: Total return percentage since inception.
        day_return_pct: Daily return percentage.
        day_pnl: Dollar amount of today's profit/loss.
        unrealized_pnl: Total unrealized profit/loss.
        realized_pnl: Total realized profit/loss (year-to-date).
        cash: Available cash balance.
        cash_pct: Cash as percentage of NAV.
        positions_count: Number of open positions.
        last_updated: Timestamp of last price update.
    """

    nav: float = Field(..., description="Current net asset value")
    total_return_pct: float = Field(..., description="Total return since inception (%)")
    day_return_pct: float = Field(..., description="Daily return (%)")
    day_pnl: float = Field(..., description="Daily P/L in dollars")
    unrealized_pnl: float = Field(..., description="Total unrealized P/L")
    realized_pnl: float = Field(..., description="YTD realized P/L")
    cash: float = Field(..., description="Available cash")
    cash_pct: float = Field(..., description="Cash as % of NAV")
    positions_count: int = Field(..., description="Number of open positions")
    last_updated: str = Field(..., description="Last update timestamp (ISO 8601)")


class Position(BaseModel):
    """Individual position response model.

    Attributes:
        symbol: Stock symbol.
        side: Position side ('long' or 'short').
        shares: Number of shares held.
        avg_cost: Average cost basis per share.
        current_price: Current market price per share.
        market_value: Current market value (shares * price).
        unrealized_pnl: Unrealized profit/loss in dollars.
        unrealized_pnl_pct: Unrealized profit/loss as percentage.
        day_change_pct: Daily price change percentage.
        weight_pct: Position weight as percentage of NAV.
        thesis_id: Associated thesis ID (if any).
        thesis_title: Associated thesis title (if any).
    """

    symbol: str = Field(..., description="Stock symbol")
    side: str = Field(..., description="Position side (long/short)")
    shares: float = Field(..., description="Number of shares")
    avg_cost: float = Field(..., description="Average cost per share")
    current_price: float = Field(..., description="Current price per share")
    market_value: float = Field(..., description="Current market value")
    unrealized_pnl: float = Field(..., description="Unrealized P/L ($)")
    unrealized_pnl_pct: float = Field(..., description="Unrealized P/L (%)")
    day_change_pct: float = Field(..., description="Daily change (%)")
    weight_pct: float = Field(..., description="Weight as % of NAV")
    thesis_id: int | None = Field(None, description="Associated thesis ID")
    thesis_title: str | None = Field(None, description="Associated thesis title")


class PositionDetail(Position):
    """Detailed position response with lot information.

    Inherits all Position fields and adds lot-level detail.
    """

    lots: list[dict[str, Any]] = Field(..., description="Individual lots")
    acquisition_dates: list[str] = Field(..., description="Lot acquisition dates")
    holding_periods: list[int] = Field(..., description="Holding periods in days")


class ExposureBreakdown(BaseModel):
    """Portfolio exposure breakdown response model.

    Attributes:
        gross_exposure: Gross exposure as percentage of NAV.
        net_exposure: Net exposure as percentage of NAV.
        long_exposure: Long exposure as percentage of NAV.
        short_exposure: Short exposure as percentage of NAV.
        by_sector: Exposure breakdown by sector.
        by_thesis: Exposure breakdown by thesis.
        concentration_risk: Largest position as percentage of NAV.
    """

    gross_exposure: float = Field(..., description="Gross exposure (% of NAV)")
    net_exposure: float = Field(..., description="Net exposure (% of NAV)")
    long_exposure: float = Field(..., description="Long exposure (% of NAV)")
    short_exposure: float = Field(..., description="Short exposure (% of NAV)")
    by_sector: dict[str, float] = Field(..., description="Exposure by sector")
    by_thesis: dict[str, float] = Field(..., description="Exposure by thesis")
    concentration_risk: float = Field(..., description="Largest position (% of NAV)")


@router.get("/status", response_model=FundStatus)
async def get_fund_status(engines: Any = Depends(get_engines)) -> FundStatus:
    """Get overall fund status and performance metrics.

    Returns high-level portfolio metrics including NAV, returns, P/L,
    and cash position. This endpoint powers the dashboard summary cards.

    Args:
        engines: Engine container with database and pricing service.

    Returns:
        FundStatus model with current portfolio metrics.
    """
    try:
        # Get latest portfolio value
        portfolio_value = engines.db.fetchone("""
            SELECT * FROM portfolio_value
            ORDER BY date DESC
            LIMIT 1
        """)

        if not portfolio_value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No portfolio data found"
            )

        # Get positions for unrealized P/L calculation
        positions = engines.db.fetchall("""
            SELECT symbol, shares, avg_cost, side
            FROM positions
            WHERE shares > 0
        """)

        # Calculate current market values
        total_market_value = 0.0
        total_unrealized_pnl = 0.0

        for position in positions:
            try:
                price_data = engines.pricing.get_price(position["symbol"])
                current_price = price_data["price"] if price_data else position["avg_cost"]

                market_value = position["shares"] * current_price
                cost_basis = position["shares"] * position["avg_cost"]

                if position["side"] == "long":
                    total_market_value += market_value
                    total_unrealized_pnl += market_value - cost_basis
                else:  # short
                    total_market_value -= market_value
                    total_unrealized_pnl += cost_basis - market_value

            except Exception as e:
                logger.warning("Failed to get price for %s: %s", position["symbol"], e)
                # Fall back to cost basis
                market_value = position["shares"] * position["avg_cost"]
                if position["side"] == "long":
                    total_market_value += market_value
                else:
                    total_market_value -= market_value

        # Current NAV
        cash = portfolio_value["cash"]
        nav = cash + total_market_value

        # Calculate returns
        cost_basis = portfolio_value["cost_basis"]
        total_return_pct = ((nav - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0.0

        # Daily return (placeholder - would need historical NAV)
        day_return_pct = 0.0  # TODO: Calculate from yesterday's NAV
        day_pnl = 0.0  # TODO: Calculate from yesterday's NAV

        # Get realized P/L (YTD)
        realized_pnl = engines.db.fetchone("""
            SELECT COALESCE(SUM(realized_pnl), 0) as total
            FROM trades
            WHERE strftime('%Y', timestamp) = strftime('%Y', 'now')
        """)
        realized_pnl_value = realized_pnl["total"] if realized_pnl else 0.0

        # Position count
        positions_count = len(positions)

        return FundStatus(
            nav=nav,
            total_return_pct=total_return_pct,
            day_return_pct=day_return_pct,
            day_pnl=day_pnl,
            unrealized_pnl=total_unrealized_pnl,
            realized_pnl=realized_pnl_value,
            cash=cash,
            cash_pct=(cash / nav * 100) if nav > 0 else 0.0,
            positions_count=positions_count,
            last_updated=datetime.now(UTC).isoformat() + "Z",
        )

    except Exception as e:
        logger.error("Failed to get fund status: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get fund status: {str(e)}",
        )


@router.get("/positions", response_model=list[Position])
async def get_positions(engines: Any = Depends(get_engines)) -> list[Position]:
    """Get all open positions with current market values.

    Returns a list of all positions with real-time pricing and P/L calculations.
    This endpoint powers the dashboard positions table.

    Args:
        engines: Engine container with database and pricing service.

    Returns:
        List of Position models with current market data.
    """
    try:
        # Get positions with thesis information
        positions = engines.db.fetchall("""
            SELECT p.*, t.title as thesis_title
            FROM positions p
            LEFT JOIN theses t ON p.thesis_id = t.id
            WHERE p.shares > 0
            ORDER BY p.symbol
        """)

        # Get current NAV for weight calculations
        portfolio_value = engines.db.fetchone("""
            SELECT * FROM portfolio_value
            ORDER BY date DESC
            LIMIT 1
        """)
        nav = portfolio_value["total_value"] if portfolio_value else 100000.0

        result = []
        for position in positions:
            try:
                # Get current price
                price_data = engines.pricing.get_price(position["symbol"])
                current_price = price_data["price"] if price_data else position["avg_cost"]
                day_change_pct = price_data.get("change_pct", 0.0) if price_data else 0.0

                # Calculate values
                shares = position["shares"]
                avg_cost = position["avg_cost"]
                market_value = shares * current_price
                cost_basis = shares * avg_cost

                if position["side"] == "long":
                    unrealized_pnl = market_value - cost_basis
                else:  # short
                    unrealized_pnl = cost_basis - market_value
                    market_value = -market_value  # Negative for shorts

                unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
                weight_pct = (abs(market_value) / nav * 100) if nav > 0 else 0.0

                result.append(
                    Position(
                        symbol=position["symbol"],
                        side=position["side"],
                        shares=shares,
                        avg_cost=avg_cost,
                        current_price=current_price,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                        unrealized_pnl_pct=unrealized_pnl_pct,
                        day_change_pct=day_change_pct,
                        weight_pct=weight_pct,
                        thesis_id=position["thesis_id"],
                        thesis_title=position["thesis_title"],
                    )
                )

            except Exception as e:
                logger.warning("Failed to process position %s: %s", position["symbol"], e)
                continue

        return result

    except Exception as e:
        logger.error("Failed to get positions: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get positions: {str(e)}",
        )


@router.get("/position/{ticker}", response_model=PositionDetail)
async def get_position_detail(ticker: str, engines: Any = Depends(get_engines)) -> PositionDetail:
    """Get detailed information for a specific position including lots.

    Args:
        ticker: Stock symbol to get details for.
        engines: Engine container with database and pricing service.

    Returns:
        PositionDetail model with lot-level information.

    Raises:
        HTTPException: If position is not found.
    """
    try:
        # Get position
        position = engines.db.fetchone(
            """
            SELECT p.*, t.title as thesis_title
            FROM positions p
            LEFT JOIN theses t ON p.thesis_id = t.id
            WHERE p.symbol = ? AND p.shares > 0
        """,
            (ticker,),
        )

        if not position:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Position {ticker} not found"
            )

        # Get lots for this position
        lots = engines.db.fetchall(
            """
            SELECT * FROM lots
            WHERE symbol = ? AND shares > 0
            ORDER BY acquired_date
        """,
            (ticker,),
        )

        # Calculate position metrics (same as get_positions)
        portfolio_value = engines.db.fetchone("""
            SELECT * FROM portfolio_value
            ORDER BY date DESC
            LIMIT 1
        """)
        nav = portfolio_value["total_value"] if portfolio_value else 100000.0

        price_data = engines.pricing.get_price(ticker)
        current_price = price_data["price"] if price_data else position["avg_cost"]
        day_change_pct = price_data.get("change_pct", 0.0) if price_data else 0.0

        shares = position["shares"]
        avg_cost = position["avg_cost"]
        market_value = shares * current_price
        cost_basis = shares * avg_cost

        if position["side"] == "long":
            unrealized_pnl = market_value - cost_basis
        else:
            unrealized_pnl = cost_basis - market_value
            market_value = -market_value

        unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
        weight_pct = (abs(market_value) / nav * 100) if nav > 0 else 0.0

        # Process lots
        lots_data = []
        acquisition_dates = []
        holding_periods = []

        for lot in lots:
            lots_data.append(
                {
                    "shares": lot["shares"],
                    "cost_basis": lot["cost_basis"],
                    "acquired_date": lot["acquired_date"],
                    "holding_period": lot["holding_period"],
                }
            )
            acquisition_dates.append(lot["acquired_date"])
            holding_periods.append(lot["holding_period"] or 0)

        return PositionDetail(
            symbol=position["symbol"],
            side=position["side"],
            shares=shares,
            avg_cost=avg_cost,
            current_price=current_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            day_change_pct=day_change_pct,
            weight_pct=weight_pct,
            thesis_id=position["thesis_id"],
            thesis_title=position["thesis_title"],
            lots=lots_data,
            acquisition_dates=acquisition_dates,
            holding_periods=holding_periods,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get position detail for %s: %s", ticker, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get position detail: {str(e)}",
        )


@router.get("/exposure", response_model=ExposureBreakdown)
async def get_exposure(engines: Any = Depends(get_engines)) -> ExposureBreakdown:
    """Get portfolio exposure breakdown by sector and thesis.

    Args:
        engines: Engine container with database and pricing service.

    Returns:
        ExposureBreakdown model with exposure analysis.
    """
    try:
        # Get current NAV
        portfolio_value = engines.db.fetchone("""
            SELECT * FROM portfolio_value
            ORDER BY date DESC
            LIMIT 1
        """)
        nav = portfolio_value["total_value"] if portfolio_value else 100000.0

        # Get positions
        positions = engines.db.fetchall("""
            SELECT p.symbol, p.shares, p.avg_cost, p.side, p.thesis_id, t.title as thesis_title
            FROM positions p
            LEFT JOIN theses t ON p.thesis_id = t.id
            WHERE p.shares > 0
        """)

        long_exposure = 0.0
        short_exposure = 0.0
        by_thesis: dict[str, float] = {}
        by_sector: dict[str, float] = {}  # TODO: Add sector classification
        max_position_value = 0.0

        for position in positions:
            try:
                price_data = engines.pricing.get_price(position["symbol"])
                current_price = price_data["price"] if price_data else position["avg_cost"]

                market_value = position["shares"] * current_price

                if position["side"] == "long":
                    long_exposure += market_value
                else:
                    short_exposure += market_value

                # Track largest position
                max_position_value = max(max_position_value, market_value)

                # Group by thesis
                thesis_title = position["thesis_title"] or "Manual"
                if thesis_title not in by_thesis:
                    by_thesis[thesis_title] = 0.0
                by_thesis[thesis_title] += market_value

                # TODO: Add sector classification
                # For now, use a placeholder sector mapping
                sector = "Technology"  # Placeholder
                if sector not in by_sector:
                    by_sector[sector] = 0.0
                by_sector[sector] += market_value

            except Exception as e:
                logger.warning(
                    "Failed to process position %s for exposure: %s", position["symbol"], e
                )
                continue

        # Convert to percentages of NAV
        gross_exposure = (long_exposure + short_exposure) / nav * 100 if nav > 0 else 0.0
        net_exposure = (long_exposure - short_exposure) / nav * 100 if nav > 0 else 0.0
        long_exposure_pct = long_exposure / nav * 100 if nav > 0 else 0.0
        short_exposure_pct = short_exposure / nav * 100 if nav > 0 else 0.0
        concentration_risk = max_position_value / nav * 100 if nav > 0 else 0.0

        # Convert thesis and sector exposures to percentages
        by_thesis_pct = {k: v / nav * 100 for k, v in by_thesis.items()} if nav > 0 else {}
        by_sector_pct = {k: v / nav * 100 for k, v in by_sector.items()} if nav > 0 else {}

        return ExposureBreakdown(
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            long_exposure=long_exposure_pct,
            short_exposure=short_exposure_pct,
            by_sector=by_sector_pct,
            by_thesis=by_thesis_pct,
            concentration_risk=concentration_risk,
        )

    except Exception as e:
        logger.error("Failed to get exposure: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get exposure: {str(e)}",
        )
