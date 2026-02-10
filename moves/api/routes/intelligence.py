"""Intelligence API endpoints: Congress trades, principles engine, what-if analysis."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.deps import EngineContainer, get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class CongressTrade(BaseModel):
    id: int
    politician: str
    symbol: str
    action: str
    amount_range: str
    date_filed: str
    date_traded: str
    days_ago: int
    portfolio_overlap: bool
    our_position_value: float | None = None
    our_position_side: str | None = None
    sentiment_signal: float = Field(..., description="0-100")
    source_url: str
    politician_score: float | None = None
    politician_tier: str | None = None
    disclosure_lag_days: int | None = Field(None, description="Days between trade and filing")
    trade_size_bucket: str | None = None
    committee_relevant: bool = False


class CongressSummary(BaseModel):
    total_trades: int
    unique_politicians: int
    unique_symbols: int
    overlap_trades: int
    net_buying_pressure: float
    top_bought_symbols: list[dict]
    top_sold_symbols: list[dict]
    recent_activity_trend: str


class Principle(BaseModel):
    id: int
    text: str
    category: str
    origin: str = Field(..., description="How created")
    validated_count: int
    invalidated_count: int
    weight: float
    validation_rate: float = Field(..., description="%")
    last_applied: str | None = None
    recent_applications: list[dict]
    active: bool
    created_at: str


class WhatIfAnalysis(BaseModel):
    signal_id: int
    symbol: str
    action: str
    decision: str = Field(..., description="rejected/ignored")
    price_at_pass: float
    current_price: float
    hypothetical_pnl: float
    hypothetical_pnl_pct: float
    days_since_pass: int
    thesis_title: str | None = None
    signal_confidence: float
    pass_accuracy: str = Field(..., description="correct/incorrect")
    regret_score: float = Field(..., description="0-100")
    created_at: str


class WhatIfSummary(BaseModel):
    total_passed_signals: int = Field(..., description="rejected + ignored")
    rejected_signals: int
    ignored_signals: int
    total_missed_pnl: float
    total_avoided_loss: float
    pass_accuracy_pct: float
    reject_accuracy_pct: float
    ignore_cost_pnl: float = Field(..., description="Cost of inattention")
    engagement_quality: float
    best_pass: dict
    worst_pass: dict


class PoliticianLeaderboardEntry(BaseModel):
    politician: str
    score: float = Field(..., description="0-100")
    tier: str
    total_trades: int = 0
    win_rate: float = 0
    trade_size_preference: str = "unknown"
    filing_delay_avg_days: float = 0


@router.get("/congress-trades/leaderboard", response_model=list[PoliticianLeaderboardEntry])
async def get_congress_leaderboard(
    limit: int = Query(20, ge=1, le=100, description="Number of politicians"),
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> list[PoliticianLeaderboardEntry]:
    try:
        from engine.congress_scoring import PoliticianScorer

        scorer = PoliticianScorer(engines.db)
        top = scorer.get_top_politicians(n=limit)
        return [
            PoliticianLeaderboardEntry(
                politician=r["politician"],
                score=r.get("score", 0),
                tier=r.get("tier", "unknown"),
                total_trades=r.get("total_trades", 0),
                win_rate=r.get("win_rate", 0),
                trade_size_preference=r.get("trade_size_preference", "unknown"),
                filing_delay_avg_days=r.get("filing_delay_avg_days", 0),
            )
            for r in top
        ]
    except Exception as e:
        logger.error("Failed to get leaderboard: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get leaderboard: {str(e)}",
        )


@router.get("/congress-trades/whales", response_model=list[CongressTrade])
async def get_whale_trades(
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> list[CongressTrade]:
    try:
        trades = engines.db.fetchall(f"""
            SELECT ct.*, ps.score as pol_score, ps.tier as pol_tier
            FROM congress_trades ct
            LEFT JOIN politician_scores ps ON ct.politician = ps.politician
            WHERE ct.date_filed >= date('now', '-{days} days')
            AND ps.tier = 'whale'
            ORDER BY ct.date_filed DESC
        """)

        from datetime import date, datetime

        result = []
        for trade in trades:
            try:
                filed_date = datetime.strptime(trade["date_filed"], "%Y-%m-%d").date()
                days_ago = (date.today() - filed_date).days
            except Exception:
                days_ago = 0

            result.append(
                CongressTrade(
                    id=trade["id"],
                    politician=trade["politician"],
                    symbol=trade["symbol"],
                    action=trade["action"],
                    amount_range=trade.get("amount_range", ""),
                    date_filed=trade["date_filed"],
                    date_traded=trade.get("date_traded", ""),
                    days_ago=days_ago,
                    portfolio_overlap=False,
                    sentiment_signal=80.0,
                    source_url=trade.get("source_url", ""),
                )
            )
        return result
    except Exception as e:
        logger.error("Failed to get whale trades: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get whale trades: {str(e)}",
        )


@router.get("/congress-trades", response_model=list[CongressTrade])
async def get_congress_trades(
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    overlap_only: bool = Query(False, description="Show only portfolio overlaps"),
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> list[CongressTrade]:
    try:
        # Get Congress trades from database with politician scores
        where_clause = f"""
            WHERE ct.date_filed >= date('now', '-{days} days')
        """

        congress_trades = engines.db.fetchall(f"""
            SELECT ct.*, ps.score as pol_score, ps.tier as pol_tier
            FROM congress_trades ct
            LEFT JOIN politician_scores ps ON ct.politician = ps.politician
            {where_clause}
            ORDER BY ct.date_filed DESC
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
                    politician_score=trade.get("pol_score"),
                    politician_tier=trade.get("pol_tier"),
                    disclosure_lag_days=trade.get("disclosure_lag_days"),
                    trade_size_bucket=trade.get("trade_size_bucket"),
                    committee_relevant=bool(trade.get("committee_relevant", 0)),
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
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> CongressSummary:
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


class PrinciplesSummary(BaseModel):
    total_active: int
    validation_rate: float = Field(..., description="0-1")
    total_validated: int
    total_invalidated: int
    last_check: str | None = None


class DiscoveredPattern(BaseModel):
    pattern_type: str
    description: str
    win_rate: float
    sample_size: int
    suggested_category: str = ""


class PrinciplesResponse(BaseModel):
    principles: list[Principle]
    summary: PrinciplesSummary
    discoveries: list[DiscoveredPattern]


@router.get("/principles", response_model=PrinciplesResponse)
async def get_principles(
    active_only: bool = Query(True, description="Show only active principles"),
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> PrinciplesResponse:
    try:
        from engine.principles import PrinciplesEngine

        pe = PrinciplesEngine(engines.db)

        # Get principles from database
        where_clause = "WHERE active = 1" if active_only else ""

        principles = engines.db.fetchall(f"""
            SELECT * FROM principles
            {where_clause}
            ORDER BY validated_count DESC, created_at DESC
        """)

        result = []
        total_validated = 0
        total_invalidated = 0
        last_applied_dates = []

        for principle in principles:
            v = principle["validated_count"]
            iv = principle["invalidated_count"]
            total_validated += v
            total_invalidated += iv
            if principle["last_applied"]:
                last_applied_dates.append(principle["last_applied"])

            # Calculate validation rate
            total_applications = v + iv
            validation_rate = (v / total_applications * 100) if total_applications > 0 else 0.0

            result.append(
                Principle(
                    id=principle["id"],
                    text=principle["text"],
                    category=principle["category"],
                    origin=principle["origin"],
                    validated_count=v,
                    invalidated_count=iv,
                    weight=principle["weight"],
                    validation_rate=validation_rate,
                    last_applied=principle["last_applied"],
                    recent_applications=[],
                    active=bool(principle["active"]),
                    created_at=principle["created_at"],
                )
            )

        # Build summary
        total_checks = total_validated + total_invalidated
        summary = PrinciplesSummary(
            total_active=len(result),
            validation_rate=(total_validated / total_checks) if total_checks > 0 else 0.0,
            total_validated=total_validated,
            total_invalidated=total_invalidated,
            last_check=max(last_applied_dates) if last_applied_dates else None,
        )

        # Discover patterns
        try:
            raw_discoveries = pe.discover_patterns()
            discoveries = [
                DiscoveredPattern(
                    pattern_type=d["pattern_type"],
                    description=d["description"],
                    win_rate=d["win_rate"],
                    sample_size=d["sample_size"],
                    suggested_category=(
                        "process"
                        if "source" in d["pattern_type"]
                        else "timing" if "strategy" in d["pattern_type"] else ""
                    ),
                )
                for d in raw_discoveries
            ]
        except Exception as e:
            logger.warning("Failed to discover patterns: %s", e)
            discoveries = []

        return PrinciplesResponse(
            principles=result,
            summary=summary,
            discoveries=discoveries,
        )

    except Exception as e:
        logger.error("Failed to get principles: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get principles: {str(e)}",
        )


class CreatePrincipleRequest(BaseModel):
    text: str
    category: str = ""
    origin: str = "user_input"


@router.post("/principles", status_code=status.HTTP_201_CREATED)
async def create_principle(
    body: CreatePrincipleRequest,
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> dict:
    try:
        from engine.principles import PrinciplesEngine

        pe = PrinciplesEngine(engines.db)
        pid = pe.create_principle(text=body.text, category=body.category, origin=body.origin)
        return {"id": pid, "status": "created"}
    except Exception as e:
        logger.error("Failed to create principle: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create principle: {str(e)}",
        )


@router.get("/what-if", response_model=list[WhatIfAnalysis])
async def get_what_if_analysis(
    days: int = Query(30, ge=1, le=365, description="Days to analyze"),
    decision_type: str | None = Query(
        None, pattern="^(rejected|ignored)$", description="Filter by decision type"
    ),
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> list[WhatIfAnalysis]:
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
            from datetime import UTC, datetime

            try:
                signal_date = datetime.fromisoformat(item["signal_created"].replace("Z", "+00:00"))
                days_since_pass = (datetime.now(UTC) - signal_date).days
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
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> WhatIfSummary:
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


class UpdatePrincipleRequest(BaseModel):
    text: str | None = None
    category: str | None = None
    weight: float | None = None
    active: bool | None = None


@router.patch("/principles/{principle_id}")
async def update_principle(
    principle_id: int,
    body: UpdatePrincipleRequest,
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> dict:
    """Update a principle's text, category, weight, or active status."""
    try:
        from engine.principles import PrinciplesEngine

        pe = PrinciplesEngine(engines.db)
        updated = pe.update_principle(principle_id, **body.model_dump(exclude_none=True))
        if not updated:
            raise HTTPException(status_code=404, detail="Principle not found or no changes")
        return {"status": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update principle: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/principles/{principle_id}")
async def delete_principle(
    principle_id: int,
    engines: EngineContainer = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a principle."""
    try:
        from engine.principles import PrinciplesEngine

        pe = PrinciplesEngine(engines.db)
        pe.delete_principle(principle_id)
        return {"status": "deleted"}
    except Exception as e:
        logger.error("Failed to delete principle: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
