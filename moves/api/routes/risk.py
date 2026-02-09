"""Risk management API endpoints.

This module provides REST API endpoints for portfolio risk analysis including
exposure limits, correlation analysis, stress testing, and macro indicators.

Endpoints:
    GET /api/fund/risk - Current risk metrics and limit status
    GET /api/fund/correlation - Position correlation matrix
    GET /api/fund/heatmap - Risk heatmap by position and sector
    GET /api/fund/macro-indicators - Economic macro indicators

These endpoints power the dashboard risk monitoring and alert systems.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses RiskManager for risk calculations and limit enforcement
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class RiskMetrics(BaseModel):
    """Portfolio risk metrics response model.

    Attributes:
        gross_exposure_pct: Gross exposure as % of NAV.
        net_exposure_pct: Net exposure as % of NAV.
        max_position_pct: Largest position as % of NAV.
        sector_concentration_pct: Largest sector as % of NAV.
        var_95_daily_pct: 95% Value at Risk daily (%).
        var_95_monthly_pct: 95% Value at Risk monthly (%).
        beta_spy: Portfolio beta vs SPY.
        correlation_spy: Portfolio correlation vs SPY.
        drawdown_current_pct: Current drawdown from peak (%).
        volatility_30d_pct: 30-day annualized volatility (%).
        downside_deviation_pct: Downside deviation (%).
        max_theoretical_loss_pct: Maximum theoretical loss (%).
        stress_test_crash_loss_pct: Loss in -20% market crash scenario (%).
        liquidity_score: Portfolio liquidity score (0-100).
    """

    gross_exposure_pct: float = Field(..., description="Gross exposure (% of NAV)")
    net_exposure_pct: float = Field(..., description="Net exposure (% of NAV)")
    max_position_pct: float = Field(..., description="Largest position (% of NAV)")
    sector_concentration_pct: float = Field(..., description="Largest sector (% of NAV)")
    var_95_daily_pct: float = Field(..., description="95% VaR daily (%)")
    var_95_monthly_pct: float = Field(..., description="95% VaR monthly (%)")
    beta_spy: float = Field(..., description="Portfolio beta vs SPY")
    correlation_spy: float = Field(..., description="Correlation vs SPY")
    drawdown_current_pct: float = Field(..., description="Current drawdown (%)")
    volatility_30d_pct: float = Field(..., description="30-day volatility (%)")
    downside_deviation_pct: float = Field(..., description="Downside deviation (%)")
    max_theoretical_loss_pct: float = Field(..., description="Max theoretical loss (%)")
    stress_test_crash_loss_pct: float = Field(..., description="20% crash loss (%)")
    liquidity_score: float = Field(..., description="Liquidity score (0-100)")


class RiskLimitStatus(BaseModel):
    """Risk limit status response model.

    Attributes:
        limit_type: Type of risk limit.
        current_value: Current value.
        limit_value: Limit threshold.
        utilization_pct: Utilization as percentage of limit.
        status: Status (safe, warning, breach).
        breach_date: Date limit was breached (if applicable).
    """

    limit_type: str = Field(..., description="Risk limit type")
    current_value: float = Field(..., description="Current value")
    limit_value: float = Field(..., description="Limit threshold")
    utilization_pct: float = Field(..., description="Utilization (%)")
    status: str = Field(..., description="Status (safe/warning/breach)")
    breach_date: str | None = Field(None, description="Breach date")


class CorrelationMatrix(BaseModel):
    """Position correlation matrix response model.

    Attributes:
        symbols: List of symbols in the matrix.
        correlations: 2D correlation matrix (symbols x symbols).
        avg_correlation: Average portfolio correlation.
        max_correlation: Maximum pairwise correlation.
        diversification_score: Diversification score (0-100).
        cluster_analysis: Position clustering results.
    """

    symbols: list[str] = Field(..., description="Symbol list")
    correlations: list[list[float]] = Field(..., description="Correlation matrix")
    avg_correlation: float = Field(..., description="Average correlation")
    max_correlation: float = Field(..., description="Maximum correlation")
    diversification_score: float = Field(..., description="Diversification score")
    cluster_analysis: dict[str, list[str]] = Field(..., description="Position clusters")


class RiskHeatmap(BaseModel):
    """Risk heatmap response model.

    Attributes:
        positions: List of position risk data.
        sectors: List of sector risk data.
        risk_buckets: Risk categorization data.
        concentration_alerts: Concentration risk alerts.
    """

    positions: list[dict] = Field(..., description="Position risk data")
    sectors: list[dict] = Field(..., description="Sector risk data")
    risk_buckets: dict[str, list[str]] = Field(..., description="Risk buckets")
    concentration_alerts: list[dict] = Field(..., description="Concentration alerts")


class MacroIndicators(BaseModel):
    """Macro economic indicators response model.

    Attributes:
        vix: VIX volatility index.
        vix_change_pct: VIX daily change (%).
        ten_year_yield: 10-year Treasury yield (%).
        ten_year_change_bp: 10-year yield change (basis points).
        dxy: Dollar index (DXY).
        dxy_change_pct: DXY daily change (%).
        oil_price: Oil price (WTI).
        oil_change_pct: Oil daily change (%).
        gold_price: Gold price per ounce.
        gold_change_pct: Gold daily change (%).
        btc_price: Bitcoin price.
        btc_change_pct: Bitcoin daily change (%).
        spy_price: SPY ETF price.
        spy_change_pct: SPY daily change (%).
        qqq_price: QQQ ETF price.
        qqq_change_pct: QQQ daily change (%).
        market_sentiment: Market sentiment score (-100 to 100).
    """

    vix: float = Field(..., description="VIX volatility index")
    vix_change_pct: float = Field(..., description="VIX change (%)")
    ten_year_yield: float = Field(..., description="10Y Treasury yield (%)")
    ten_year_change_bp: float = Field(..., description="10Y change (bp)")
    dxy: float = Field(..., description="Dollar index")
    dxy_change_pct: float = Field(..., description="DXY change (%)")
    oil_price: float = Field(..., description="Oil price (WTI)")
    oil_change_pct: float = Field(..., description="Oil change (%)")
    gold_price: float = Field(..., description="Gold price")
    gold_change_pct: float = Field(..., description="Gold change (%)")
    btc_price: float = Field(..., description="Bitcoin price")
    btc_change_pct: float = Field(..., description="BTC change (%)")
    spy_price: float = Field(..., description="SPY price")
    spy_change_pct: float = Field(..., description="SPY change (%)")
    qqq_price: float = Field(..., description="QQQ price")
    qqq_change_pct: float = Field(..., description="QQQ change (%)")
    market_sentiment: float = Field(..., description="Market sentiment (-100 to 100)")


@router.get("/risk", response_model=dict)
async def get_risk_metrics(
    engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> dict:
    """Get comprehensive portfolio risk metrics and limit status.

    Returns current risk metrics, limit utilization, and risk alerts.
    This endpoint powers the dashboard risk monitoring section.

    Args:
        engines: Engine container with risk manager.

    Returns:
        Dictionary containing risk metrics and limit status.
    """
    try:
        # Get current portfolio exposure
        exposure = engines.risk_manager.calculate_exposure()

        # Get risk limits status
        limits_status = []
        limits = engines.db.fetchall("SELECT * FROM risk_limits WHERE enabled = 1")

        for limit in limits:
            limit_type = limit["limit_type"]
            limit_value = limit["value"]

            # Calculate current value based on limit type
            if limit_type == "max_position_pct":
                current_value = exposure.get("max_position_pct", 0.0)
            elif limit_type == "max_sector_pct":
                current_value = exposure.get("max_sector_pct", 0.0)
            elif limit_type == "max_gross_exposure":
                current_value = exposure.get("gross_exposure", 0.0)
            elif limit_type == "net_exposure_min":
                current_value = exposure.get("net_exposure", 0.0)
            elif limit_type == "net_exposure_max":
                current_value = exposure.get("net_exposure", 0.0)
            else:
                current_value = 0.0  # Other limits would need separate calculations

            # Calculate utilization and status
            if limit_type in ["net_exposure_min"]:
                utilization_pct = (
                    abs(current_value - limit_value) / abs(limit_value) * 100
                    if limit_value != 0
                    else 0.0
                )
                breached = current_value < limit_value
            else:
                utilization_pct = (current_value / limit_value * 100) if limit_value > 0 else 0.0
                breached = current_value > limit_value

            status = "breach" if breached else ("warning" if utilization_pct > 80 else "safe")

            limits_status.append(
                RiskLimitStatus(
                    limit_type=limit_type,
                    current_value=current_value,
                    limit_value=limit_value,
                    utilization_pct=utilization_pct,
                    status=status,
                    breach_date=None,  # Would track in separate table
                )
            )

        # Calculate risk metrics
        risk_metrics = RiskMetrics(
            gross_exposure_pct=exposure.get("gross_exposure", 0.0) * 100,
            net_exposure_pct=exposure.get("net_exposure", 0.0) * 100,
            max_position_pct=exposure.get("max_position_pct", 0.0) * 100,
            sector_concentration_pct=exposure.get("max_sector_pct", 0.0) * 100,
            var_95_daily_pct=2.1,  # Placeholder
            var_95_monthly_pct=9.8,  # Placeholder
            beta_spy=1.15,  # Placeholder
            correlation_spy=0.82,  # Placeholder
            drawdown_current_pct=1.2,  # Placeholder
            volatility_30d_pct=18.5,  # Placeholder
            downside_deviation_pct=12.8,  # Placeholder
            max_theoretical_loss_pct=45.2,  # Placeholder
            stress_test_crash_loss_pct=18.7,  # Placeholder
            liquidity_score=85.0,  # Placeholder
        )

        return {
            "metrics": risk_metrics.dict(),
            "limits": [limit.dict() for limit in limits_status],
            "alerts": [
                limit.dict() for limit in limits_status if limit.status in ["warning", "breach"]
            ],
        }

    except Exception as e:
        logger.error("Failed to get risk metrics: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get risk metrics: {str(e)}",
        )


@router.get("/correlation", response_model=CorrelationMatrix)
async def get_correlation_matrix(
    engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> CorrelationMatrix:
    """Get position correlation matrix and diversification analysis.

    Calculates pairwise correlations between portfolio positions and
    provides diversification scoring and cluster analysis.

    Args:
        engines: Engine container with database and pricing service.

    Returns:
        CorrelationMatrix model with correlation data.
    """
    try:
        # Get current positions
        positions = engines.db.fetchall("""
            SELECT symbol FROM positions
            WHERE shares > 0
            ORDER BY symbol
        """)

        if len(positions) < 2:
            # Return empty matrix for single or no positions
            return CorrelationMatrix(
                symbols=[],
                correlations=[],
                avg_correlation=0.0,
                max_correlation=0.0,
                diversification_score=100.0,
                cluster_analysis={},
            )

        symbols = [p["symbol"] for p in positions]

        # Compute correlations from 3-month daily returns
        from engine import pricing

        returns_by_symbol: dict[str, list[float]] = {}
        for sym in symbols:
            hist = pricing.get_history(sym, period="3mo")
            if len(hist) >= 2:
                closes = [h["close"] for h in hist]
                rets = [
                    (closes[i] - closes[i - 1]) / closes[i - 1]
                    for i in range(1, len(closes))
                    if closes[i - 1] != 0
                ]
                returns_by_symbol[sym] = rets

        def _pearson(a: list[float], b: list[float]) -> float:
            n = min(len(a), len(b))
            if n < 5:
                return 0.0
            a, b = a[:n], b[:n]
            ma = sum(a) / n
            mb = sum(b) / n
            cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
            sa = math.sqrt(sum((x - ma) ** 2 for x in a))
            sb = math.sqrt(sum((x - mb) ** 2 for x in b))
            if sa == 0 or sb == 0:
                return 0.0
            return cov / (sa * sb)

        correlations = []
        for i in range(len(symbols)):
            row = []
            for j in range(len(symbols)):
                if i == j:
                    row.append(1.0)
                elif i > j:
                    row.append(correlations[j][i])
                else:
                    ra = returns_by_symbol.get(symbols[i], [])
                    rb = returns_by_symbol.get(symbols[j], [])
                    corr = _pearson(ra, rb)
                    row.append(round(corr, 3))
            correlations.append(row)

        # Calculate statistics
        all_correlations = []
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                all_correlations.append(correlations[i][j])

        avg_correlation = sum(all_correlations) / len(all_correlations) if all_correlations else 0.0
        max_correlation = max(all_correlations) if all_correlations else 0.0

        # Diversification score (inverse of average correlation)
        diversification_score = max(0, (1 - avg_correlation) * 100)

        # Simple clustering (group highly correlated positions)
        cluster_analysis = {}
        high_corr_threshold = 0.7

        for i, symbol1 in enumerate(symbols):
            cluster_key = f"cluster_{i}"
            cluster = [symbol1]

            for j, symbol2 in enumerate(symbols):
                if i != j and correlations[i][j] > high_corr_threshold:
                    if symbol2 not in [
                        s for cluster_list in cluster_analysis.values() for s in cluster_list
                    ]:
                        cluster.append(symbol2)

            if len(cluster) > 1:
                cluster_analysis[cluster_key] = cluster

        return CorrelationMatrix(
            symbols=symbols,
            correlations=correlations,
            avg_correlation=round(avg_correlation, 3),
            max_correlation=round(max_correlation, 3),
            diversification_score=round(diversification_score, 1),
            cluster_analysis=cluster_analysis,
        )

    except Exception as e:
        logger.error("Failed to get correlation matrix: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get correlation matrix: {str(e)}",
        )


@router.get("/heatmap", response_model=RiskHeatmap)
async def get_risk_heatmap(
    engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> RiskHeatmap:
    """Get risk heatmap data for visualization.

    Provides position and sector risk data formatted for heatmap
    visualization with risk bucketing and concentration alerts.

    Args:
        engines: Engine container with database and pricing service.

    Returns:
        RiskHeatmap model with visualization data.
    """
    try:
        # Get positions with risk metrics
        positions = engines.db.fetchall("""
            SELECT p.*, t.title as thesis_title
            FROM positions p
            LEFT JOIN theses t ON p.thesis_id = t.id
            WHERE p.shares > 0
        """)

        # Get current NAV
        portfolio_value = engines.db.fetchone("""
            SELECT total_value FROM portfolio_value
            ORDER BY date DESC
            LIMIT 1
        """)
        nav = portfolio_value["total_value"] if portfolio_value else 100000.0

        position_data = []
        sector_data = {}
        risk_buckets = {"low": [], "medium": [], "high": [], "extreme": []}
        concentration_alerts = []

        for position in positions:
            symbol = position["symbol"]
            shares = position["shares"]
            avg_cost = position["avg_cost"]

            # Get current price
            try:
                price_data = engines.pricing.get_price(symbol)
                current_price = price_data["price"] if price_data else avg_cost
            except Exception:
                current_price = avg_cost

            market_value = shares * current_price
            weight_pct = (market_value / nav * 100) if nav > 0 else 0.0

            # Calculate risk metrics
            unrealized_pnl = (current_price - avg_cost) * shares
            unrealized_pnl_pct = (
                (unrealized_pnl / (shares * avg_cost) * 100) if shares * avg_cost > 0 else 0.0
            )

            # Compute 30-day annualized volatility from price history
            from engine import pricing as _pricing

            hist = _pricing.get_history(symbol, period="3mo")
            if len(hist) >= 2:
                closes = [h["close"] for h in hist]
                daily_rets = [
                    (closes[i] - closes[i - 1]) / closes[i - 1]
                    for i in range(1, len(closes))
                    if closes[i - 1] != 0
                ]
                # Use last 30 trading days or whatever is available
                recent_rets = daily_rets[-30:] if len(daily_rets) > 30 else daily_rets
                if recent_rets:
                    mean_r = sum(recent_rets) / len(recent_rets)
                    var = sum((r - mean_r) ** 2 for r in recent_rets) / len(recent_rets)
                    vol_annual = math.sqrt(var) * math.sqrt(252) * 100
                else:
                    vol_annual = 25.0
            else:
                vol_annual = 25.0

            # Get sector from fundamentals
            fundamentals = _pricing.get_fundamentals(symbol)
            position_sector = fundamentals.get("sector") or "Unknown"

            # Risk score based on weight and volatility
            risk_score = weight_pct * 2 + vol_annual * 0.1 + abs(unrealized_pnl_pct) * 0.05

            # Risk bucket classification
            if risk_score < 5:
                risk_bucket = "low"
            elif risk_score < 15:
                risk_bucket = "medium"
            elif risk_score < 30:
                risk_bucket = "high"
            else:
                risk_bucket = "extreme"

            risk_buckets[risk_bucket].append(symbol)

            position_data.append(
                {
                    "symbol": symbol,
                    "weight_pct": round(weight_pct, 2),
                    "market_value": market_value,
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                    "risk_score": round(risk_score, 2),
                    "risk_bucket": risk_bucket,
                    "thesis_title": position["thesis_title"],
                    "volatility": round(vol_annual, 1),
                }
            )

            # Check for concentration alerts
            if weight_pct > 15:  # 15% concentration threshold
                concentration_alerts.append(
                    {
                        "symbol": symbol,
                        "weight_pct": weight_pct,
                        "alert_type": "position_concentration",
                        "message": f"{symbol} represents {weight_pct:.1f}% of portfolio",
                    }
                )

            # Group by sector from fundamentals
            sector = position_sector
            if sector not in sector_data:
                sector_data[sector] = {
                    "sector": sector,
                    "weight_pct": 0.0,
                    "positions": 0,
                    "avg_risk_score": 0.0,
                    "risk_scores": [],
                }

            sector_data[sector]["weight_pct"] += weight_pct
            sector_data[sector]["positions"] += 1
            sector_data[sector]["risk_scores"].append(risk_score)

        # Calculate average risk scores for sectors
        sectors = []
        for sector_info in sector_data.values():
            sector_info["avg_risk_score"] = sum(sector_info["risk_scores"]) / len(
                sector_info["risk_scores"]
            )
            del sector_info["risk_scores"]  # Remove temporary field
            sectors.append(sector_info)

            # Check for sector concentration
            if sector_info["weight_pct"] > 35:  # 35% sector concentration threshold
                concentration_alerts.append(
                    {
                        "sector": sector_info["sector"],
                        "weight_pct": sector_info["weight_pct"],
                        "alert_type": "sector_concentration",
                        "message": (
                            f"{sector_info['sector']} sector represents "
                            f"{sector_info['weight_pct']:.1f}% of portfolio"
                        ),
                    }
                )

        return RiskHeatmap(
            positions=position_data,
            sectors=sectors,
            risk_buckets=risk_buckets,
            concentration_alerts=concentration_alerts,
        )

    except Exception as e:
        logger.error("Failed to get risk heatmap: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get risk heatmap: {str(e)}",
        )


@router.get("/macro-indicators", response_model=MacroIndicators)
async def get_macro_indicators(
    engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> MacroIndicators:
    """Get current macro economic indicators.

    Fetches key macro indicators that affect portfolio risk including
    volatility indices, rates, currencies, and commodities.

    Args:
        engines: Engine container with pricing service.

    Returns:
        MacroIndicators model with current indicator values.
    """
    try:
        from engine import pricing as _pricing

        # Fetch live prices for macro symbols
        macro_symbols = {
            "vix": "^VIX",
            "ten_year": "^TNX",
            "dxy": "DX-Y.NYB",
            "oil": "CL=F",
            "gold": "GC=F",
            "btc": "BTC-USD",
            "spy": "SPY",
            "qqq": "QQQ",
        }

        prices = {}
        for key, sym in macro_symbols.items():
            try:
                data = _pricing.get_price(sym)
                prices[key] = {
                    "price": data.get("price", 0) if data else 0,
                    "change_pct": data.get("change_pct", 0) if data else 0,
                }
            except Exception:
                prices[key] = {"price": 0, "change_pct": 0}

        vix_price = prices["vix"]["price"]
        # Market sentiment: VIX < 15 = bullish (positive), > 25 = bearish (negative)
        sentiment = max(-100, min(100, (20 - vix_price) * 5)) if vix_price > 0 else 0.0

        indicators = MacroIndicators(
            vix=vix_price,
            vix_change_pct=prices["vix"]["change_pct"],
            ten_year_yield=prices["ten_year"]["price"],
            ten_year_change_bp=prices["ten_year"]["change_pct"] * 100,
            dxy=prices["dxy"]["price"],
            dxy_change_pct=prices["dxy"]["change_pct"],
            oil_price=prices["oil"]["price"],
            oil_change_pct=prices["oil"]["change_pct"],
            gold_price=prices["gold"]["price"],
            gold_change_pct=prices["gold"]["change_pct"],
            btc_price=prices["btc"]["price"],
            btc_change_pct=prices["btc"]["change_pct"],
            spy_price=prices["spy"]["price"],
            spy_change_pct=prices["spy"]["change_pct"],
            qqq_price=prices["qqq"]["price"],
            qqq_change_pct=prices["qqq"]["change_pct"],
            market_sentiment=round(sentiment, 1),
        )

        return indicators

    except Exception as e:
        logger.error("Failed to get macro indicators: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get macro indicators: {str(e)}",
        )
