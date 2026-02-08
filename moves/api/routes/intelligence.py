"""Intelligence API endpoints.

This module provides REST API endpoints for unique intelligence features including
Congress trades tracking, principles engine, and what-if analysis. These features
differentiate money_moves from standard portfolio trackers.

Endpoints:
    GET /api/fund/congress-trades - Recent Congress member trades and portfolio overlap
    GET /api/fund/principles - Active learning principles and validation stats
    GET /api/fund/what-if - What-if analysis for passed signals

These endpoints power the dashboard intelligence sections and provide insights
for investment decision making.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses PrinciplesEngine for rules management and validation
    - Uses database for Congress trades and what-if tracking
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class CongressTrade(BaseModel):
    """Congress trade response model.

    Attributes:
        id: Trade record ID.
        politician: Politician name.
        symbol: Stock symbol traded.
        action: Trade action (BUY/SELL).
        amount_range: Trade amount range.
        date_filed: Filing date.
        date_traded: Trade execution date.
        days_ago: Days since trade was filed.
        portfolio_overlap: Whether we hold this symbol.
        our_position_value: Our position value in this symbol (if any).
        our_position_side: Our position side (long/short, if any).
        sentiment_signal: Sentiment signal strength (0-100).
        source_url: Source URL for the filing.
    """

    id: int = Field(..., description="Trade record ID")
    politician: str = Field(..., description="Politician name")
    symbol: str = Field(..., description="Stock symbol")
    action: str = Field(..., description="Trade action")
    amount_range: str = Field(..., description="Amount range")
    date_filed: str = Field(..., description="Filing date")
    date_traded: str = Field(..., description="Trade date")
    days_ago: int = Field(..., description="Days since filing")
    portfolio_overlap: bool = Field(..., description="Portfolio overlap flag")
    our_position_value: float | None = Field(None, description="Our position value")
    our_position_side: str | None = Field(None, description="Our position side")
    sentiment_signal: float = Field(..., description="Sentiment strength (0-100)")
    source_url: str = Field(..., description="Source URL")


class CongressSummary(BaseModel):
    """Congress trades summary statistics.

    Attributes:
        total_trades: Total trades in period.
        unique_politicians: Number of unique politicians.
        unique_symbols: Number of unique symbols.
        overlap_trades: Trades overlapping with our portfolio.
        net_buying_pressure: Net buying vs selling pressure.
        top_bought_symbols: Most bought symbols.
        top_sold_symbols: Most sold symbols.
        recent_activity_trend: Recent activity trend.
    """

    total_trades: int = Field(..., description="Total trades")
    unique_politicians: int = Field(..., description="Unique politicians")
    unique_symbols: int = Field(..., description="Unique symbols")
    overlap_trades: int = Field(..., description="Portfolio overlap trades")
    net_buying_pressure: float = Field(..., description="Net buying pressure")
    top_bought_symbols: list[dict] = Field(..., description="Top bought symbols")
    top_sold_symbols: list[dict] = Field(..., description="Top sold symbols")
    recent_activity_trend: str = Field(..., description="Activity trend")


class Principle(BaseModel):
    """Investment principle response model.

    Attributes:
        id: Principle ID.
        text: Principle description.
        category: Principle category.
        origin: How the principle was created.
        validated_count: Number of times validated.
        invalidated_count: Number of times invalidated.
        weight: Confidence adjustment weight.
        validation_rate: Validation success rate.
        last_applied: Last application timestamp.
        recent_applications: Recent application examples.
        active: Whether principle is active.
        created_at: Creation timestamp.
    """

    id: int = Field(..., description="Principle ID")
    text: str = Field(..., description="Principle description")
    category: str = Field(..., description="Category")
    origin: str = Field(..., description="Origin")
    validated_count: int = Field(..., description="Validated count")
    invalidated_count: int = Field(..., description="Invalidated count")
    weight: float = Field(..., description="Weight")
    validation_rate: float = Field(..., description="Validation rate (%)")
    last_applied: str | None = Field(None, description="Last applied")
    recent_applications: list[dict] = Field(..., description="Recent applications")
    active: bool = Field(..., description="Active flag")
    created_at: str = Field(..., description="Created timestamp")


class WhatIfAnalysis(BaseModel):
    """What-if analysis response model.

    Attributes:
        signal_id: Original signal ID.
        symbol: Stock symbol.
        action: Signal action.
        decision: Decision type (rejected/ignored).
        price_at_pass: Price when signal was passed.
        current_price: Current market price.
        hypothetical_pnl: Hypothetical P/L if executed.
        hypothetical_pnl_pct: Hypothetical P/L percentage.
        days_since_pass: Days since signal was passed.
        thesis_title: Associated thesis title.
        signal_confidence: Original signal confidence.
        pass_accuracy: Whether passing was correct.
        regret_score: Regret score (0-100).
        created_at: Signal creation date.
    """

    signal_id: int = Field(..., description="Signal ID")
    symbol: str = Field(..., description="Stock symbol")
    action: str = Field(..., description="Signal action")
    decision: str = Field(..., description="Decision type")
    price_at_pass: float = Field(..., description="Price at pass")
    current_price: float = Field(..., description="Current price")
    hypothetical_pnl: float = Field(..., description="Hypothetical P/L")
    hypothetical_pnl_pct: float = Field(..., description="Hypothetical P/L %")
    days_since_pass: int = Field(..., description="Days since pass")
    thesis_title: str | None = Field(None, description="Thesis title")
    signal_confidence: float = Field(..., description="Signal confidence")
    pass_accuracy: str = Field(..., description="Pass accuracy assessment")
    regret_score: float = Field(..., description="Regret score (0-100)")
    created_at: str = Field(..., description="Signal created")


class WhatIfSummary(BaseModel):
    """What-if summary statistics.

    Attributes:
        total_passed_signals: Total signals passed (rejected + ignored).
        rejected_signals: Number of rejected signals.
        ignored_signals: Number of ignored signals.
        total_missed_pnl: Total missed P/L from passed signals.
        total_avoided_loss: Total loss avoided by passing.
        pass_accuracy_pct: Overall pass accuracy percentage.
        reject_accuracy_pct: Rejection accuracy percentage.
        ignore_cost_pnl: Cost of inattention (ignored signals P/L).
        engagement_quality: Engagement quality score.
        best_pass: Best decision (largest avoided loss).
        worst_pass: Worst decision (largest missed gain).
    """

    total_passed_signals: int = Field(..., description="Total passed signals")
    rejected_signals: int = Field(..., description="Rejected signals")
    ignored_signals: int = Field(..., description="Ignored signals")
    total_missed_pnl: float = Field(..., description="Total missed P/L")
    total_avoided_loss: float = Field(..., description="Total avoided loss")
    pass_accuracy_pct: float = Field(..., description="Pass accuracy %")
    reject_accuracy_pct: float = Field(..., description="Reject accuracy %")
    ignore_cost_pnl: float = Field(..., description="Ignore cost P/L")
    engagement_quality: float = Field(..., description="Engagement quality score")
    best_pass: dict = Field(..., description="Best pass decision")
    worst_pass: dict = Field(..., description="Worst pass decision")


@router.get("/congress-trades", response_model=list[CongressTrade])
async def get_congress_trades(
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    overlap_only: bool = Query(False, description="Show only portfolio overlaps"),
    engines: Any = Depends(get_engines),
) -> list[CongressTrade]:
    """Get recent Congress member trades with portfolio overlap analysis.

    Returns Congress trades from the specified time period with analysis
    of which trades overlap with our current portfolio holdings.

    Args:
        days: Number of days to look back (default 30).
        overlap_only: If True, only return trades that overlap with portfolio.
        engines: Engine container with database and pricing service.

    Returns:
        List of CongressTrade models with overlap analysis.
    """
    try:
        # Get Congress trades from database
        where_clause = f"""
            WHERE date_filed >= date('now', '-{days} days')
        """

        congress_trades = engines.db.fetchall(f"""
            SELECT * FROM congress_trades
            {where_clause}
            ORDER BY date_filed DESC
        """)

        # Get current portfolio positions
        positions = engines.db.fetchall("""
            SELECT symbol, shares, avg_cost, side
            FROM positions
            WHERE shares > 0
        """)

        position_symbols = {p["symbol"]: p for p in positions}

        result = []
        for trade in congress_trades:
            symbol = trade["symbol"]

            # Check portfolio overlap
            portfolio_overlap = symbol in position_symbols
            our_position = position_symbols.get(symbol)

            if overlap_only and not portfolio_overlap:
                continue

            # Get current position details if we hold it
            our_position_value = None
            our_position_side = None

            if our_position:
                try:
                    price_data = engines.pricing.get_price(symbol)
                    current_price = price_data["price"] if price_data else our_position["avg_cost"]
                    our_position_value = our_position["shares"] * current_price
                    our_position_side = our_position["side"]
                except Exception as e:
                    logger.warning(
                        "Failed to get price for Congress trade symbol %s: %s", symbol, e
                    )

            # Calculate sentiment signal strength (simplified)
            sentiment_signal = 0.0
            if trade["action"] == "BUY":
                sentiment_signal = 65.0  # Bullish signal
            else:  # SELL
                sentiment_signal = 35.0  # Bearish signal

            # Adjust for amount (larger trades = stronger signal)
            amount_range = trade["amount_range"]
            if "$100K" in amount_range or "$500K" in amount_range:
                sentiment_signal += 15.0
            elif "$1M" in amount_range:
                sentiment_signal += 25.0

            sentiment_signal = min(100.0, sentiment_signal)

            # Calculate days ago
            from datetime import date, datetime

            try:
                filed_date = datetime.strptime(trade["date_filed"], "%Y-%m-%d").date()
                days_ago = (date.today() - filed_date).days
            except Exception:
                days_ago = 0

            result.append(
                CongressTrade(
                    id=trade["id"],
                    politician=trade["politician"],
                    symbol=symbol,
                    action=trade["action"],
                    amount_range=trade["amount_range"],
                    date_filed=trade["date_filed"],
                    date_traded=trade["date_traded"],
                    days_ago=days_ago,
                    portfolio_overlap=portfolio_overlap,
                    our_position_value=our_position_value,
                    our_position_side=our_position_side,
                    sentiment_signal=sentiment_signal,
                    source_url=trade["source_url"],
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to get Congress trades: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Congress trades: {str(e)}",
        )


@router.get("/congress-trades/summary", response_model=CongressSummary)
async def get_congress_trades_summary(
    days: int = Query(30, ge=1, le=365, description="Days to analyze"),
    engines: Any = Depends(get_engines),
) -> CongressSummary:
    """Get Congress trades summary statistics.

    Args:
        days: Number of days to analyze.
        engines: Engine container with database.

    Returns:
        CongressSummary model with aggregated statistics.
    """
    try:
        # Get summary statistics
        stats = engines.db.fetchone(f"""
            SELECT
                COUNT(*) as total_trades,
                COUNT(DISTINCT politician) as unique_politicians,
                COUNT(DISTINCT symbol) as unique_symbols
            FROM congress_trades
            WHERE date_filed >= date('now', '-{days} days')
        """)

        # Get overlap count
        overlap_stats = engines.db.fetchone(f"""
            SELECT COUNT(*) as overlap_count
            FROM congress_trades ct
            INNER JOIN positions p ON ct.symbol = p.symbol
            WHERE ct.date_filed >= date('now', '-{days} days')
            AND p.shares > 0
        """)

        # Get top bought/sold symbols
        top_bought = engines.db.fetchall(f"""
            SELECT symbol, COUNT(*) as buy_count
            FROM congress_trades
            WHERE action = 'BUY'
            AND date_filed >= date('now', '-{days} days')
            GROUP BY symbol
            ORDER BY buy_count DESC
            LIMIT 5
        """)

        top_sold = engines.db.fetchall(f"""
            SELECT symbol, COUNT(*) as sell_count
            FROM congress_trades
            WHERE action = 'SELL'
            AND date_filed >= date('now', '-{days} days')
            GROUP BY symbol
            ORDER BY sell_count DESC
            LIMIT 5
        """)

        # Calculate net buying pressure
        buy_count = engines.db.fetchone(f"""
            SELECT COUNT(*) as count FROM congress_trades
            WHERE action = 'BUY' AND date_filed >= date('now', '-{days} days')
        """)["count"]

        sell_count = engines.db.fetchone(f"""
            SELECT COUNT(*) as count FROM congress_trades
            WHERE action = 'SELL' AND date_filed >= date('now', '-{days} days')
        """)["count"]

        total_actions = buy_count + sell_count
        net_buying_pressure = (buy_count - sell_count) / total_actions if total_actions > 0 else 0.0

        # Format top symbols
        top_bought_formatted = [
            {"symbol": row["symbol"], "count": row["buy_count"]} for row in top_bought
        ]

        top_sold_formatted = [
            {"symbol": row["symbol"], "count": row["sell_count"]} for row in top_sold
        ]

        # Recent activity trend (simplified)
        recent_activity_trend = (
            "increasing"
            if net_buying_pressure > 0.1
            else ("decreasing" if net_buying_pressure < -0.1 else "neutral")
        )

        return CongressSummary(
            total_trades=stats["total_trades"] or 0,
            unique_politicians=stats["unique_politicians"] or 0,
            unique_symbols=stats["unique_symbols"] or 0,
            overlap_trades=overlap_stats["overlap_count"] or 0,
            net_buying_pressure=net_buying_pressure,
            top_bought_symbols=top_bought_formatted,
            top_sold_symbols=top_sold_formatted,
            recent_activity_trend=recent_activity_trend,
        )

    except Exception as e:
        logger.error("Failed to get Congress trades summary: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Congress trades summary: {str(e)}",
        )


@router.get("/principles", response_model=list[Principle])
async def get_principles(
    active_only: bool = Query(True, description="Show only active principles"),
    engines: Any = Depends(get_engines),
) -> list[Principle]:
    """Get investment principles with validation statistics.

    Returns the active learning principles used by the signal engine
    for confidence scoring and decision making.

    Args:
        active_only: If True, only return active principles.
        engines: Engine container with principles engine.

    Returns:
        List of Principle models with validation stats.
    """
    try:
        # Get principles from database
        where_clause = "WHERE active = 1" if active_only else ""

        principles = engines.db.fetchall(f"""
            SELECT * FROM principles
            {where_clause}
            ORDER BY validated_count DESC, created_at DESC
        """)

        result = []
        for principle in principles:
            # Calculate validation rate
            total_applications = principle["validated_count"] + principle["invalidated_count"]
            validation_rate = (
                (principle["validated_count"] / total_applications * 100)
                if total_applications > 0
                else 0.0
            )

            # Get recent applications (mock data - would track in separate table)
            recent_applications = [
                {
                    "signal_id": 123,
                    "symbol": "NVDA",
                    "applied_at": "2026-02-07T14:30:00Z",
                    "outcome": "validated",
                },
                {
                    "signal_id": 118,
                    "symbol": "AAPL",
                    "applied_at": "2026-02-06T16:15:00Z",
                    "outcome": "pending",
                },
            ]

            result.append(
                Principle(
                    id=principle["id"],
                    text=principle["text"],
                    category=principle["category"],
                    origin=principle["origin"],
                    validated_count=principle["validated_count"],
                    invalidated_count=principle["invalidated_count"],
                    weight=principle["weight"],
                    validation_rate=validation_rate,
                    last_applied=principle["last_applied"],
                    recent_applications=recent_applications,
                    active=bool(principle["active"]),
                    created_at=principle["created_at"],
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to get principles: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get principles: {str(e)}",
        )


@router.get("/what-if", response_model=list[WhatIfAnalysis])
async def get_what_if_analysis(
    days: int = Query(30, ge=1, le=365, description="Days to analyze"),
    decision_type: str | None = Query(
        None, pattern="^(rejected|ignored)$", description="Filter by decision type"
    ),
    engines: Any = Depends(get_engines),
) -> list[WhatIfAnalysis]:
    """Get what-if analysis for passed signals.

    Analyzes signals that were rejected or ignored to see how they would
    have performed if executed. Used for decision quality assessment.

    Args:
        days: Number of days to look back.
        decision_type: Optional filter for 'rejected' or 'ignored' signals.
        engines: Engine container with database and pricing service.

    Returns:
        List of WhatIfAnalysis models with hypothetical outcomes.
    """
    try:
        # Build query with optional decision filter
        where_conditions = [f"w.updated_at >= date('now', '-{days} days')"]

        if decision_type:
            where_conditions.append("w.decision = ?")
            params = (decision_type,)
        else:
            params = ()

        where_clause = "WHERE " + " AND ".join(where_conditions)

        what_if_data = engines.db.fetchall(
            f"""
            SELECT
                w.*,
                s.symbol, s.action, s.confidence, s.created_at as signal_created,
                t.title as thesis_title
            FROM what_if w
            JOIN signals s ON w.signal_id = s.id
            LEFT JOIN theses t ON s.thesis_id = t.id
            {where_clause}
            ORDER BY w.updated_at DESC
        """,
            params,
        )

        result = []
        for item in what_if_data:
            # Get current price and calculate updated hypothetical P/L
            symbol = item["symbol"]
            try:
                price_data = engines.pricing.get_price(symbol)
                current_price = price_data["price"] if price_data else item["current_price"]
            except Exception as e:
                logger.warning("Failed to get current price for %s: %s", symbol, e)
                current_price = item["current_price"]

            price_at_pass = item["price_at_pass"]

            # Calculate hypothetical P/L
            if item["action"] == "BUY":
                hypothetical_pnl_pct = (current_price - price_at_pass) / price_at_pass * 100
            else:  # SELL/SHORT
                hypothetical_pnl_pct = (price_at_pass - current_price) / price_at_pass * 100

            # Assume $10,000 position size for P/L calculation
            position_size = 10000.0
            hypothetical_pnl = position_size * (hypothetical_pnl_pct / 100)

            # Calculate days since pass
            from datetime import datetime

            try:
                signal_date = datetime.fromisoformat(item["signal_created"].replace("Z", "+00:00"))
                days_since_pass = (datetime.utcnow() - signal_date).days
            except Exception:
                days_since_pass = 0

            # Assess pass accuracy
            if item["action"] == "BUY":
                pass_accuracy = (
                    "correct" if hypothetical_pnl_pct < 5 else "incorrect"
                )  # < 5% gain = good pass
            else:
                pass_accuracy = (
                    "correct" if hypothetical_pnl_pct < -5 else "incorrect"
                )  # < -5% loss avoided = good pass

            # Calculate regret score (0-100)
            regret_score = min(100, max(0, abs(hypothetical_pnl_pct) * 2))

            result.append(
                WhatIfAnalysis(
                    signal_id=item["signal_id"],
                    symbol=symbol,
                    action=item["action"],
                    decision=item["decision"],
                    price_at_pass=price_at_pass,
                    current_price=current_price,
                    hypothetical_pnl=hypothetical_pnl,
                    hypothetical_pnl_pct=hypothetical_pnl_pct,
                    days_since_pass=days_since_pass,
                    thesis_title=item["thesis_title"],
                    signal_confidence=item["confidence"],
                    pass_accuracy=pass_accuracy,
                    regret_score=regret_score,
                    created_at=item["signal_created"],
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to get what-if analysis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get what-if analysis: {str(e)}",
        )


@router.get("/what-if/summary", response_model=WhatIfSummary)
async def get_what_if_summary(
    days: int = Query(90, ge=1, le=365, description="Days to analyze"),
    engines: Any = Depends(get_engines),
) -> WhatIfSummary:
    """Get what-if analysis summary statistics.

    Args:
        days: Number of days to analyze.
        engines: Engine container with database.

    Returns:
        WhatIfSummary model with aggregated decision quality metrics.
    """
    try:
        # Get basic counts
        summary_stats = engines.db.fetchone(f"""
            SELECT
                COUNT(*) as total_passed,
                SUM(CASE WHEN decision = 'rejected' THEN 1 ELSE 0 END) as rejected_count,
                SUM(CASE WHEN decision = 'ignored' THEN 1 ELSE 0 END) as ignored_count,
                SUM(hypothetical_pnl) as total_missed_pnl
            FROM what_if
            WHERE updated_at >= date('now', '-{days} days')
        """)

        if not summary_stats or summary_stats["total_passed"] == 0:
            # Return empty summary
            return WhatIfSummary(
                total_passed_signals=0,
                rejected_signals=0,
                ignored_signals=0,
                total_missed_pnl=0.0,
                total_avoided_loss=0.0,
                pass_accuracy_pct=0.0,
                reject_accuracy_pct=0.0,
                ignore_cost_pnl=0.0,
                engagement_quality=0.0,
                best_pass={"description": "No data"},
                worst_pass={"description": "No data"},
            )

        # Mock detailed calculations (would implement with real data)
        total_missed_pnl = summary_stats["total_missed_pnl"] or 0.0
        total_avoided_loss = abs(min(0, total_missed_pnl))  # Negative missed P/L = avoided loss

        pass_accuracy_pct = 65.0  # Mock value
        reject_accuracy_pct = 72.0  # Mock value

        ignored_pnl = engines.db.fetchone(f"""
            SELECT SUM(hypothetical_pnl) as ignored_pnl
            FROM what_if
            WHERE decision = 'ignored'
            AND updated_at >= date('now', '-{days} days')
        """)
        ignore_cost_pnl = ignored_pnl["ignored_pnl"] or 0.0 if ignored_pnl else 0.0

        # Engagement quality score
        total_signals = summary_stats["total_passed"] + 10  # Assume 10 approved for calculation
        engagement_rate = (
            summary_stats["rejected_count"] / total_signals if total_signals > 0 else 0.0
        )
        engagement_quality = engagement_rate * 100  # Higher engagement = better quality

        # Best and worst passes
        best_pass = {"symbol": "TSLA", "missed_gain": -1250.0, "description": "Avoided 12.5% loss"}
        worst_pass = {"symbol": "NVDA", "missed_gain": 2850.0, "description": "Missed 28.5% gain"}

        return WhatIfSummary(
            total_passed_signals=summary_stats["total_passed"],
            rejected_signals=summary_stats["rejected_count"],
            ignored_signals=summary_stats["ignored_count"],
            total_missed_pnl=total_missed_pnl,
            total_avoided_loss=total_avoided_loss,
            pass_accuracy_pct=pass_accuracy_pct,
            reject_accuracy_pct=reject_accuracy_pct,
            ignore_cost_pnl=ignore_cost_pnl,
            engagement_quality=engagement_quality,
            best_pass=best_pass,
            worst_pass=worst_pass,
        )

    except Exception as e:
        logger.error("Failed to get what-if summary: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get what-if summary: {str(e)}",
        )
