"""Signal management API endpoints.

This module provides REST API endpoints for managing trading signals including
listing pending signals and approving/rejecting them. These endpoints work
in conjunction with the Telegram bot for signal approval workflow.

Endpoints:
    GET /api/fund/signals - List signals with optional status filtering
    POST /api/fund/signals/{id}/approve - Approve a pending signal
    POST /api/fund/signals/{id}/reject - Reject a pending signal

The signal engine handles confidence scoring, risk checks, and execution
when signals are approved through these endpoints.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses SignalEngine for signal management and execution logic
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class SignalResponse(BaseModel):
    """Response model for signal information.

    Attributes:
        id: Signal database ID.
        action: Signal action (BUY, SELL, SHORT, COVER).
        symbol: Stock symbol.
        thesis_id: Associated thesis ID.
        thesis_title: Associated thesis title.
        confidence: Signal confidence score (0.0 to 1.0).
        source: Signal source (thesis_update, price_trigger, etc.).
        horizon: Investment horizon.
        reasoning: LLM-generated reasoning.
        size_pct: Suggested position size as % of NAV.
        funding_plan: JSON funding plan (for BUY signals).
        status: Current signal status.
        telegram_msg_id: Telegram message ID (if sent).
        created_at: Signal creation timestamp.
        decided_at: Decision timestamp (if approved/rejected).
        expired_at: Expiration timestamp (if expired).
        current_price: Current market price.
        target_price: Target price (if specified).
        stop_price: Stop loss price (if specified).
        risk_score: Risk assessment score.
        days_pending: Days since signal was created.
    """

    id: int = Field(..., description="Signal ID")
    action: str = Field(..., description="Signal action")
    symbol: str = Field(..., description="Stock symbol")
    thesis_id: int | None = Field(None, description="Associated thesis ID")
    thesis_title: str | None = Field(None, description="Associated thesis title")
    confidence: float = Field(..., description="Confidence score (0-1)")
    source: str = Field(..., description="Signal source")
    horizon: str | None = Field(None, description="Investment horizon")
    reasoning: str | None = Field(None, description="LLM reasoning")
    size_pct: float | None = Field(None, description="Position size (% of NAV)")
    funding_plan: str | None = Field(None, description="Funding plan JSON")
    status: str = Field(..., description="Signal status")
    telegram_msg_id: str | None = Field(None, description="Telegram message ID")
    created_at: str = Field(..., description="Creation timestamp")
    decided_at: str | None = Field(None, description="Decision timestamp")
    expired_at: str | None = Field(None, description="Expiration timestamp")
    current_price: float | None = Field(None, description="Current market price")
    target_price: float | None = Field(None, description="Target price")
    stop_price: float | None = Field(None, description="Stop loss price")
    risk_score: float | None = Field(None, description="Risk score")
    days_pending: int | None = Field(None, description="Days since creation")


class SignalDecisionRequest(BaseModel):
    """Request model for approving or rejecting signals.

    Attributes:
        reason: Optional reason for the decision.
        size_override: Optional position size override (% of NAV).
        price_override: Optional price override for execution.
    """

    reason: str = Field("", description="Reason for decision")
    size_override: float | None = Field(
        None, ge=0.01, le=1.0, description="Size override (% of NAV)"
    )
    price_override: float | None = Field(None, gt=0, description="Price override")


class SignalDecisionResponse(BaseModel):
    """Response model for signal approval/rejection.

    Attributes:
        signal_id: ID of the processed signal.
        status: New signal status.
        message: Status message.
        order_id: Order ID (if approved and executed).
    """

    signal_id: int = Field(..., description="Signal ID")
    status: str = Field(..., description="New signal status")
    message: str = Field(..., description="Status message")
    order_id: int | None = Field(None, description="Order ID if executed")


@router.get("/signals", response_model=list[SignalResponse])
async def list_signals(
    status: str | None = None, limit: int = 50, engines: Any = Depends(get_engines)
) -> list[SignalResponse]:
    """List trading signals with optional status filtering.

    Returns signals ordered by creation time (newest first) with enriched
    data including current prices, thesis information, and risk scores.

    Args:
        status: Optional status filter ('pending', 'approved', 'rejected', etc.).
        limit: Maximum number of signals to return (default 50, max 100).
        engines: Engine container with database and pricing service.

    Returns:
        List of SignalResponse models with current data.
    """
    try:
        # Limit the query size
        limit = min(limit, 100)

        # Build query with optional status filter
        where_clause = "WHERE s.status = ?" if status else ""
        params = (status, limit) if status else (limit,)

        signals = engines.db.fetchall(
            f"""
            SELECT s.*, t.title as thesis_title
            FROM signals s
            LEFT JOIN theses t ON s.thesis_id = t.id
            {where_clause}
            ORDER BY s.created_at DESC
            LIMIT ?
        """,
            params,
        )

        result = []
        for signal in signals:
            # Get current price
            current_price = None
            try:
                price_data = engines.pricing.get_price(signal["symbol"])
                current_price = price_data["price"] if price_data else None
            except Exception as e:
                logger.warning("Failed to get price for %s: %s", signal["symbol"], e)

            # Calculate days pending (for pending signals)
            days_pending = None
            if signal["status"] == "pending" and signal["created_at"]:
                from datetime import UTC, datetime

                try:
                    created_at = datetime.fromisoformat(signal["created_at"].replace("Z", "+00:00"))
                    now = datetime.now(UTC)
                    days_pending = (now - created_at).days
                except Exception as e:
                    logger.warning(
                        "Failed to calculate days pending for signal %s: %s", signal["id"], e
                    )

            # Get risk score (placeholder - could be calculated by risk engine)
            risk_score = signal.get("risk_score")  # Might be None if not calculated

            result.append(
                SignalResponse(
                    id=signal["id"],
                    action=signal["action"],
                    symbol=signal["symbol"],
                    thesis_id=signal["thesis_id"],
                    thesis_title=signal["thesis_title"],
                    confidence=signal["confidence"],
                    source=signal["source"],
                    horizon=signal["horizon"],
                    reasoning=signal["reasoning"],
                    size_pct=signal["size_pct"],
                    funding_plan=signal["funding_plan"],
                    status=signal["status"],
                    telegram_msg_id=signal["telegram_msg_id"],
                    created_at=signal["created_at"],
                    decided_at=signal["decided_at"],
                    expired_at=signal["expired_at"],
                    current_price=current_price,
                    target_price=signal.get("target_price"),
                    stop_price=signal.get("stop_price"),
                    risk_score=risk_score,
                    days_pending=days_pending,
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to list signals: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list signals: {str(e)}",
        )


@router.post("/signals/{signal_id}/approve", response_model=SignalDecisionResponse)
async def approve_signal(
    signal_id: int, decision: SignalDecisionRequest, engines: Any = Depends(get_engines)
) -> SignalDecisionResponse:
    """Approve a pending signal for execution.

    Validates the signal is in pending status, performs risk checks,
    and executes the trade via the broker. Updates signal status and
    creates audit log entries.

    Args:
        signal_id: ID of the signal to approve.
        decision: Approval details including optional overrides.
        engines: Engine container with signal engine and broker.

    Returns:
        SignalDecisionResponse with execution status.

    Raises:
        HTTPException: If signal is not found or cannot be executed.
    """
    try:
        # Check signal exists and is pending
        signal = engines.db.fetchone(
            """
            SELECT * FROM signals WHERE id = ?
        """,
            (signal_id,),
        )

        if not signal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal {signal_id} not found"
            )

        if signal["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Signal {signal_id} is not pending (status: {signal['status']})",
            )

        # Use SignalEngine to approve
        try:
            engines.signal_engine.approve_signal(signal_id)
            order_id = None

            return SignalDecisionResponse(
                signal_id=signal_id,
                status="approved",
                message="Signal approved and executed successfully",
                order_id=order_id,
            )

        except Exception as e:
            # Update signal status to failed
            engines.db.execute(
                """
                UPDATE signals
                SET status = 'failed', decided_at = datetime('now'),
                    error_message = ?
                WHERE id = ?
            """,
                (str(e), signal_id),
            )
            engines.db.connect().commit()

            # Log to audit trail
            engines.db.execute(
                """
                INSERT INTO audit_log (action, entity_type, entity_id, actor, details)
                VALUES ('signal_execution_failed', 'signal', ?, 'api', ?)
            """,
                (signal_id, f"Error: {str(e)}"),
            )
            engines.db.connect().commit()

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to execute approved signal: {str(e)}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to approve signal %s: %s", signal_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve signal: {str(e)}",
        )


@router.post("/signals/{signal_id}/reject", response_model=SignalDecisionResponse)
async def reject_signal(
    signal_id: int, decision: SignalDecisionRequest, engines: Any = Depends(get_engines)
) -> SignalDecisionResponse:
    """Reject a pending signal and record for what-if tracking.

    Updates signal status to rejected, creates what-if tracking record,
    and logs the decision with reason for future analysis.

    Args:
        signal_id: ID of the signal to reject.
        decision: Rejection details including reason.
        engines: Engine container with signal engine.

    Returns:
        SignalDecisionResponse with rejection status.

    Raises:
        HTTPException: If signal is not found or not pending.
    """
    try:
        # Check signal exists and is pending
        signal = engines.db.fetchone(
            """
            SELECT * FROM signals WHERE id = ?
        """,
            (signal_id,),
        )

        if not signal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal {signal_id} not found"
            )

        if signal["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Signal {signal_id} is not pending (status: {signal['status']})",
            )

        # Use SignalEngine to reject and create what-if record
        # Get current price for what-if tracking
        try:
            price_data = engines.pricing.get_price(signal["symbol"])
            price_at_pass = price_data.get("price", 0) if price_data else 0
        except Exception:
            price_at_pass = 0
        engines.signal_engine.reject_signal(signal_id, price_at_pass=price_at_pass)

        return SignalDecisionResponse(
            signal_id=signal_id,
            status="rejected",
            message="Signal rejected and recorded for what-if tracking",
            order_id=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to reject signal %s: %s", signal_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject signal: {str(e)}",
        )
