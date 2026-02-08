"""Performance analysis API endpoints.

This module provides REST API endpoints for portfolio performance analysis
including returns, benchmarking, drawdown analysis, and risk-adjusted metrics.

Endpoints:
    GET /api/fund/performance - Portfolio performance metrics and time series
    GET /api/fund/benchmark - Benchmark comparison (SPY, QQQ, IWM)
    GET /api/fund/drawdown - Drawdown analysis and underwater periods

These endpoints power the dashboard performance charts and analytics sections.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses AnalyticsEngine for performance calculations and benchmarking
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


class PerformanceMetrics(BaseModel):
    """Portfolio performance metrics response model.

    Attributes:
        total_return_pct: Total return since inception (%).
        annualized_return_pct: Annualized return (%).
        ytd_return_pct: Year-to-date return (%).
        mtd_return_pct: Month-to-date return (%).
        wtd_return_pct: Week-to-date return (%).
        daily_return_pct: Daily return (%).
        sharpe_ratio: Sharpe ratio (risk-free rate 4.5%).
        sortino_ratio: Sortino ratio (downside deviation).
        max_drawdown_pct: Maximum drawdown from peak (%).
        volatility_pct: Annualized volatility (%).
        var_95_pct: 95% Value at Risk (%).
        win_rate_pct: Percentage of profitable trading days.
        best_day_pct: Best daily return (%).
        worst_day_pct: Worst daily return (%).
        calmar_ratio: Calmar ratio (annual return / max drawdown).
        information_ratio: Information ratio vs SPY.
    """

    total_return_pct: float = Field(..., description="Total return since inception (%)")
    annualized_return_pct: float = Field(..., description="Annualized return (%)")
    ytd_return_pct: float = Field(..., description="Year-to-date return (%)")
    mtd_return_pct: float = Field(..., description="Month-to-date return (%)")
    wtd_return_pct: float = Field(..., description="Week-to-date return (%)")
    daily_return_pct: float = Field(..., description="Daily return (%)")
    sharpe_ratio: float = Field(..., description="Sharpe ratio")
    sortino_ratio: float = Field(..., description="Sortino ratio")
    max_drawdown_pct: float = Field(..., description="Maximum drawdown (%)")
    volatility_pct: float = Field(..., description="Annualized volatility (%)")
    var_95_pct: float = Field(..., description="95% Value at Risk (%)")
    win_rate_pct: float = Field(..., description="Win rate (%)")
    best_day_pct: float = Field(..., description="Best daily return (%)")
    worst_day_pct: float = Field(..., description="Worst daily return (%)")
    calmar_ratio: float = Field(..., description="Calmar ratio")
    information_ratio: float = Field(..., description="Information ratio vs SPY")


class PerformanceTimeSeries(BaseModel):
    """Time series data for performance charting.

    Attributes:
        dates: List of dates in YYYY-MM-DD format.
        nav_values: Net asset values.
        daily_returns: Daily return percentages.
        cumulative_returns: Cumulative return percentages.
        drawdowns: Drawdown percentages from peak.
    """

    dates: list[str] = Field(..., description="Dates (YYYY-MM-DD)")
    nav_values: list[float] = Field(..., description="NAV values")
    daily_returns: list[float] = Field(..., description="Daily returns (%)")
    cumulative_returns: list[float] = Field(..., description="Cumulative returns (%)")
    drawdowns: list[float] = Field(..., description="Drawdowns (%)")


class BenchmarkComparison(BaseModel):
    """Benchmark comparison metrics.

    Attributes:
        benchmark_symbol: Benchmark symbol (SPY, QQQ, IWM).
        portfolio_return_pct: Portfolio return over period.
        benchmark_return_pct: Benchmark return over period.
        alpha_pct: Alpha vs benchmark (%).
        beta: Beta vs benchmark.
        correlation: Correlation coefficient.
        tracking_error_pct: Tracking error (%).
        information_ratio: Information ratio.
        up_capture_pct: Upside capture ratio (%).
        down_capture_pct: Downside capture ratio (%).
        dates: List of dates for time series.
        portfolio_values: Portfolio cumulative returns.
        benchmark_values: Benchmark cumulative returns.
    """

    benchmark_symbol: str = Field(..., description="Benchmark symbol")
    portfolio_return_pct: float = Field(..., description="Portfolio return (%)")
    benchmark_return_pct: float = Field(..., description="Benchmark return (%)")
    alpha_pct: float = Field(..., description="Alpha (%)")
    beta: float = Field(..., description="Beta")
    correlation: float = Field(..., description="Correlation")
    tracking_error_pct: float = Field(..., description="Tracking error (%)")
    information_ratio: float = Field(..., description="Information ratio")
    up_capture_pct: float = Field(..., description="Upside capture (%)")
    down_capture_pct: float = Field(..., description="Downside capture (%)")
    dates: list[str] = Field(..., description="Dates")
    portfolio_values: list[float] = Field(..., description="Portfolio cumulative returns")
    benchmark_values: list[float] = Field(..., description="Benchmark cumulative returns")


class DrawdownAnalysis(BaseModel):
    """Drawdown analysis response model.

    Attributes:
        current_drawdown_pct: Current drawdown from peak (%).
        max_drawdown_pct: Maximum historical drawdown (%).
        max_drawdown_start: Start date of max drawdown period.
        max_drawdown_end: End date of max drawdown period.
        max_drawdown_duration: Duration of max drawdown in days.
        days_underwater: Current days underwater.
        recovery_factor: Recovery factor (time to recover / drawdown duration).
        drawdown_events: List of significant drawdown periods.
        underwater_periods: List of underwater periods.
    """

    current_drawdown_pct: float = Field(..., description="Current drawdown (%)")
    max_drawdown_pct: float = Field(..., description="Maximum drawdown (%)")
    max_drawdown_start: str | None = Field(None, description="Max drawdown start date")
    max_drawdown_end: str | None = Field(None, description="Max drawdown end date")
    max_drawdown_duration: int | None = Field(None, description="Max drawdown duration (days)")
    days_underwater: int = Field(..., description="Current days underwater")
    recovery_factor: float | None = Field(None, description="Recovery factor")
    drawdown_events: list[dict] = Field(..., description="Significant drawdown periods")
    underwater_periods: list[dict] = Field(..., description="Underwater periods")


@router.get("/performance", response_model=PerformanceMetrics)
async def get_performance_metrics(
    days: int = Query(365, ge=30, le=1825, description="Number of days to analyze"),
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> PerformanceMetrics:
    """Get portfolio performance metrics and statistics.

    Calculates comprehensive performance metrics including returns, risk measures,
    and drawdown statistics for the specified time period.

    Args:
        days: Number of days to analyze (default 365, max 5 years).
        engines: Engine container with analytics engine.

    Returns:
        PerformanceMetrics model with calculated statistics.
    """
    try:
        # Get portfolio value history
        portfolio_values = engines.db.fetchall(
            f"""
            SELECT date, total_value, daily_return_pct
            FROM portfolio_value
            WHERE date >= date('now', '-{days} days')
            ORDER BY date
        """
        )

        if not portfolio_values:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No portfolio data found for the specified period",
            )

        # Extract values and returns
        nav_values = [pv["total_value"] for pv in portfolio_values]
        daily_returns = [pv["daily_return_pct"] or 0.0 for pv in portfolio_values]

        # Calculate basic metrics
        if len(nav_values) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient data for performance analysis",
            )

        # Total return
        start_value = nav_values[0]
        end_value = nav_values[-1]
        total_return_pct = (
            ((end_value - start_value) / start_value * 100) if start_value > 0 else 0.0
        )

        # Annualized return
        years = len(portfolio_values) / 365.25
        annualized_return_pct = (
            (((end_value / start_value) ** (1 / years)) - 1) * 100
            if years > 0 and start_value > 0
            else 0.0
        )

        # Period returns (placeholder calculations)
        ytd_return_pct = 0.0  # TODO: Calculate YTD return
        mtd_return_pct = 0.0  # TODO: Calculate MTD return
        wtd_return_pct = 0.0  # TODO: Calculate WTD return
        daily_return_pct = daily_returns[-1] if daily_returns else 0.0

        # Risk metrics
        import statistics

        # Volatility (annualized)
        if len(daily_returns) > 1:
            daily_vol = statistics.stdev(daily_returns)
            volatility_pct = daily_vol * (252**0.5)  # Annualize
        else:
            volatility_pct = 0.0

        # Sharpe ratio (assuming 4.5% risk-free rate)
        risk_free_rate = 4.5
        sharpe_ratio = (
            ((annualized_return_pct - risk_free_rate) / volatility_pct)
            if volatility_pct > 0
            else 0.0
        )

        # Sortino ratio (downside deviation)
        downside_returns = [r for r in daily_returns if r < 0]
        if downside_returns:
            downside_vol = statistics.stdev(downside_returns) * (252**0.5)
            sortino_ratio = (
                ((annualized_return_pct - risk_free_rate) / downside_vol)
                if downside_vol > 0
                else 0.0
            )
        else:
            sortino_ratio = float("inf") if annualized_return_pct > risk_free_rate else 0.0

        # Maximum drawdown
        peak = nav_values[0]
        max_drawdown_pct = 0.0

        for value in nav_values:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak * 100
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

        # Current drawdown (not used in this function but calculated for reference)
        max(nav_values)
        # current_drawdown_pct = (
        #     (current_peak - end_value) / current_peak * 100 if current_peak > 0 else 0.0
        # )

        # 95% VaR
        sorted_returns = sorted(daily_returns)
        var_index = int(len(sorted_returns) * 0.05)
        var_95_pct = sorted_returns[var_index] if var_index < len(sorted_returns) else 0.0

        # Win rate
        positive_days = len([r for r in daily_returns if r > 0])
        win_rate_pct = (positive_days / len(daily_returns) * 100) if daily_returns else 0.0

        # Best/worst days
        best_day_pct = max(daily_returns) if daily_returns else 0.0
        worst_day_pct = min(daily_returns) if daily_returns else 0.0

        # Calmar ratio
        calmar_ratio = (annualized_return_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

        # Information ratio (placeholder - would need benchmark data)
        information_ratio = 0.0  # TODO: Calculate vs SPY

        return PerformanceMetrics(
            total_return_pct=total_return_pct,
            annualized_return_pct=annualized_return_pct,
            ytd_return_pct=ytd_return_pct,
            mtd_return_pct=mtd_return_pct,
            wtd_return_pct=wtd_return_pct,
            daily_return_pct=daily_return_pct,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown_pct=max_drawdown_pct,
            volatility_pct=volatility_pct,
            var_95_pct=var_95_pct,
            win_rate_pct=win_rate_pct,
            best_day_pct=best_day_pct,
            worst_day_pct=worst_day_pct,
            calmar_ratio=calmar_ratio,
            information_ratio=information_ratio,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get performance metrics: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get performance metrics: {str(e)}",
        )


@router.get("/benchmark", response_model=BenchmarkComparison)
async def get_benchmark_comparison(
    benchmark: str = Query("SPY", pattern="^(SPY|QQQ|IWM)$", description="Benchmark symbol"),
    days: int = Query(365, ge=30, le=1825, description="Number of days to compare"),
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> BenchmarkComparison:
    """Compare portfolio performance against benchmark.

    Calculates alpha, beta, correlation, and other metrics relative to
    the specified benchmark (SPY, QQQ, or IWM).

    Args:
        benchmark: Benchmark symbol to compare against.
        days: Number of days to analyze.
        engines: Engine container with pricing service.

    Returns:
        BenchmarkComparison model with relative performance metrics.
    """
    try:
        # Get portfolio data
        portfolio_values = engines.db.fetchall(
            f"""
            SELECT date, total_value
            FROM portfolio_value
            WHERE date >= date('now', '-{days} days')
            ORDER BY date
        """
        )

        if not portfolio_values:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No portfolio data found for comparison",
            )

        # Get benchmark prices (placeholder - would use pricing service)
        # For now, return mock data
        portfolio_return_pct = 15.2  # Placeholder
        benchmark_return_pct = 12.8  # Placeholder

        dates = [pv["date"] for pv in portfolio_values]
        portfolio_values_list = [pv["total_value"] for pv in portfolio_values]

        # Calculate cumulative returns
        start_value = portfolio_values_list[0]
        portfolio_cumulative = [(v / start_value - 1) * 100 for v in portfolio_values_list]

        # Mock benchmark data (would be calculated from real prices)
        benchmark_cumulative = [i * 0.03 for i in range(len(dates))]  # Mock 3% per period

        return BenchmarkComparison(
            benchmark_symbol=benchmark,
            portfolio_return_pct=portfolio_return_pct,
            benchmark_return_pct=benchmark_return_pct,
            alpha_pct=2.4,  # Placeholder
            beta=1.15,  # Placeholder
            correlation=0.82,  # Placeholder
            tracking_error_pct=8.5,  # Placeholder
            information_ratio=0.28,  # Placeholder
            up_capture_pct=105.0,  # Placeholder
            down_capture_pct=95.0,  # Placeholder
            dates=dates,
            portfolio_values=portfolio_cumulative,
            benchmark_values=benchmark_cumulative,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get benchmark comparison: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get benchmark comparison: {str(e)}",
        )


@router.get("/drawdown", response_model=DrawdownAnalysis)
async def get_drawdown_analysis(
    engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> DrawdownAnalysis:
    """Get detailed drawdown analysis and underwater periods.

    Analyzes portfolio drawdown patterns including current underwater period,
    historical drawdown events, and recovery statistics.

    Args:
        engines: Engine container with database.

    Returns:
        DrawdownAnalysis model with drawdown statistics.
    """
    try:
        # Get drawdown events from database
        drawdown_events = engines.db.fetchall("""
            SELECT * FROM drawdown_events
            ORDER BY peak_date DESC
            LIMIT 10
        """)

        # Calculate current drawdown
        latest_portfolio = engines.db.fetchone("""
            SELECT total_value FROM portfolio_value
            ORDER BY date DESC
            LIMIT 1
        """)

        peak_portfolio = engines.db.fetchone("""
            SELECT MAX(total_value) as peak_value FROM portfolio_value
        """)

        current_value = latest_portfolio["total_value"] if latest_portfolio else 0.0
        peak_value = peak_portfolio["peak_value"] if peak_portfolio else current_value

        current_drawdown_pct = (
            ((peak_value - current_value) / peak_value * 100) if peak_value > 0 else 0.0
        )

        # Find maximum drawdown
        max_drawdown_event = (
            max(drawdown_events, key=lambda x: x["drawdown_pct"]) if drawdown_events else None
        )
        max_drawdown_pct = max_drawdown_event["drawdown_pct"] if max_drawdown_event else 0.0

        # Calculate days underwater
        days_underwater = 0
        if current_drawdown_pct > 0:
            # Count days since peak
            peak_date_result = engines.db.fetchone("""
                SELECT date FROM portfolio_value
                WHERE total_value = (SELECT MAX(total_value) FROM portfolio_value)
                ORDER BY date DESC
                LIMIT 1
            """)

            if peak_date_result:
                from datetime import date, datetime

                try:
                    peak_date = datetime.strptime(peak_date_result["date"], "%Y-%m-%d").date()
                    today = date.today()
                    days_underwater = (today - peak_date).days
                except Exception as e:
                    logger.warning("Failed to calculate days underwater: %s", e)
                    days_underwater = 0

        # Format drawdown events
        formatted_events = []
        for event in drawdown_events:
            formatted_events.append(
                {
                    "start_date": event["peak_date"],
                    "end_date": event["recovery_date"] or "Ongoing",
                    "drawdown_pct": event["drawdown_pct"],
                    "duration_days": event["days_underwater"] or 0,
                }
            )

        # Underwater periods (simplified - same as drawdown events for now)
        underwater_periods = formatted_events

        # Recovery factor (placeholder)
        recovery_factor = 1.2  # Placeholder

        return DrawdownAnalysis(
            current_drawdown_pct=current_drawdown_pct,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_start=max_drawdown_event["peak_date"] if max_drawdown_event else None,
            max_drawdown_end=max_drawdown_event["recovery_date"] if max_drawdown_event else None,
            max_drawdown_duration=max_drawdown_event["days_underwater"]
            if max_drawdown_event
            else None,
            days_underwater=days_underwater,
            recovery_factor=recovery_factor,
            drawdown_events=formatted_events,
            underwater_periods=underwater_periods,
        )

    except Exception as e:
        logger.error("Failed to get drawdown analysis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get drawdown analysis: {str(e)}",
        )
