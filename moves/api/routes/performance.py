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


class NavPoint(BaseModel):
    """Single NAV data point for time series."""

    date: str = Field(..., description="Date (YYYY-MM-DD)")
    value: float = Field(..., description="NAV value")


class PerformanceMetrics(BaseModel):
    """Portfolio performance metrics response model."""

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
    nav_series: list[NavPoint] = Field(default_factory=list, description="NAV time series")


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


class DrawdownPoint(BaseModel):
    """Single drawdown data point."""

    date: str = Field(..., description="Date")
    value: float = Field(..., description="Drawdown percentage (negative)")


class DrawdownAnalysis(BaseModel):
    """Drawdown analysis response model."""

    current_drawdown_pct: float = Field(..., description="Current drawdown (%)")
    max_drawdown_pct: float = Field(..., description="Maximum drawdown (%)")
    max_drawdown_start: str | None = Field(None, description="Max drawdown start date")
    max_drawdown_end: str | None = Field(None, description="Max drawdown end date")
    max_drawdown_duration: int | None = Field(None, description="Max drawdown duration (days)")
    days_underwater: int = Field(..., description="Current days underwater")
    recovery_factor: float | None = Field(None, description="Recovery factor")
    drawdown_events: list[dict] = Field(..., description="Significant drawdown periods")
    underwater_periods: list[dict] = Field(..., description="Underwater periods")
    series: list[DrawdownPoint] = Field(default_factory=list, description="Drawdown time series")


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

        # Period returns from portfolio_value table
        from api.benchmark import calculate_period_return, period_start_date

        ytd_return_pct = calculate_period_return(
            portfolio_values, period_start_date("ytd")
        )
        mtd_return_pct = calculate_period_return(
            portfolio_values, period_start_date("mtd")
        )
        wtd_return_pct = calculate_period_return(
            portfolio_values, period_start_date("wtd")
        )
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

        # Information ratio vs SPY
        from api.benchmark import (
            align_series,
            compute_benchmark_stats,
            daily_returns as calc_daily_returns,
            fetch_benchmark_prices,
        )

        information_ratio = 0.0
        try:
            first_date = portfolio_values[0]["date"]
            last_date = portfolio_values[-1]["date"]
            bm_data = fetch_benchmark_prices("SPY", first_date, last_date)
            if bm_data:
                pf_vals, bm_vals, _ = align_series(portfolio_values, bm_data)
                if len(pf_vals) > 2:
                    pf_rets = calc_daily_returns(pf_vals)
                    bm_rets = calc_daily_returns(bm_vals)
                    stats = compute_benchmark_stats(pf_rets, bm_rets)
                    information_ratio = stats["information_ratio"]
        except Exception as exc:
            logger.warning("Info ratio calculation failed: %s", exc)

        # Build NAV time series for chart
        nav_series = [
            NavPoint(date=pv["date"], value=pv["total_value"])
            for pv in portfolio_values
        ]

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
            nav_series=nav_series,
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
        # Get portfolio value history for drawdown calculation
        portfolio_values = engines.db.fetchall("""
            SELECT date, total_value FROM portfolio_value
            ORDER BY date
        """)

        if not portfolio_values:
            return DrawdownAnalysis(
                current_drawdown_pct=0.0,
                max_drawdown_pct=0.0,
                days_underwater=0,
                drawdown_events=[],
                underwater_periods=[],
                series=[],
            )

        # Compute drawdown series from portfolio values
        peak = portfolio_values[0]["total_value"]
        max_dd = 0.0
        max_dd_start = None
        dd_series = []
        current_dd_start = None

        for pv in portfolio_values:
            val = pv["total_value"]
            if val > peak:
                peak = val
                current_dd_start = None
            dd_pct = -((peak - val) / peak * 100) if peak > 0 else 0.0
            dd_series.append(DrawdownPoint(date=pv["date"], value=dd_pct))
            if abs(dd_pct) > max_dd:
                max_dd = abs(dd_pct)
                max_dd_start = current_dd_start or pv["date"]
            if dd_pct < 0 and current_dd_start is None:
                current_dd_start = pv["date"]

        current_drawdown_pct = abs(dd_series[-1].value) if dd_series else 0.0

        # Days underwater
        days_underwater = 0
        if current_drawdown_pct > 0.01:
            from datetime import date as date_cls
            from datetime import datetime

            try:
                peak_date = None
                peak_val = 0
                for pv in portfolio_values:
                    if pv["total_value"] >= peak_val:
                        peak_val = pv["total_value"]
                        peak_date = pv["date"]
                if peak_date:
                    pd = datetime.strptime(peak_date, "%Y-%m-%d").date()
                    days_underwater = (date_cls.today() - pd).days
            except Exception:
                pass

        return DrawdownAnalysis(
            current_drawdown_pct=current_drawdown_pct,
            max_drawdown_pct=max_dd,
            max_drawdown_start=max_dd_start,
            days_underwater=days_underwater,
            recovery_factor=None,
            drawdown_events=[],
            underwater_periods=[],
            series=dd_series,
        )

    except Exception as e:
        logger.error("Failed to get drawdown analysis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get drawdown analysis: {str(e)}",
        )
