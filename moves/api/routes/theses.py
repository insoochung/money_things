"""Thesis management API endpoints.

This module provides REST API endpoints for managing investment theses including
creation, retrieval, and status updates. These endpoints support both manual
thesis entry and integration with the money_thoughts module.

Endpoints:
    GET /api/fund/theses - List all theses with status and performance
    POST /api/fund/theses - Create a new thesis (from money_thoughts or manual)
    PUT /api/fund/theses/{id} - Update thesis status or details

The thesis engine handles validation, state transitions, and ticker discovery
automatically when theses are created or updated.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses ThesisEngine for thesis management logic
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class ThesisRequest(BaseModel):
    """Request model for creating or updating a thesis.

    Attributes:
        title: Brief thesis title/headline.
        thesis_text: Full thesis description and reasoning.
        strategy: Investment strategy ('long', 'short', 'long_short').
        symbols: List of ticker symbols aligned with the thesis.
        universe_keywords: Keywords for automatic ticker discovery.
        validation_criteria: List of criteria that would validate the thesis.
        failure_criteria: List of criteria that would invalidate the thesis.
        horizon: Investment horizon ('1d', '1w', '1m', '3m', '6m', '1y').
        conviction: Confidence level (0.0 to 1.0).
        source_module: Source module ('money_thoughts', 'manual').
    """

    title: str = Field(..., min_length=1, max_length=200, description="Thesis title")
    thesis_text: str = Field(..., min_length=10, description="Full thesis description")
    strategy: str = Field(
        ..., pattern="^(long|short|long_short)$", description="Investment strategy"
    )
    symbols: list[str] = Field(..., description="Initial ticker symbols")
    universe_keywords: list[str] = Field(
        default_factory=list, description="Keywords for ticker discovery"
    )
    validation_criteria: list[str] = Field(default_factory=list, description="Validation criteria")
    failure_criteria: list[str] = Field(default_factory=list, description="Failure criteria")
    horizon: str = Field("6m", pattern="^(1d|1w|1m|3m|6m|1y)$", description="Investment horizon")
    conviction: float = Field(..., ge=0.0, le=1.0, description="Conviction level (0-1)")
    source_module: str = Field("manual", description="Source module")


class ThesisUpdate(BaseModel):
    """Request model for updating thesis status.

    Attributes:
        status: New thesis status.
        reason: Reason for status change.
        evidence: Supporting evidence for the change.
    """

    status: str = Field(
        ..., pattern="^(active|strengthening|confirmed|weakening|invalidated|archived)$"
    )
    reason: str = Field(..., description="Reason for status change")
    evidence: str = Field(default="", description="Supporting evidence")


class ThesisResponse(BaseModel):
    """Response model for thesis information.

    Attributes:
        id: Thesis database ID.
        title: Thesis title.
        thesis_text: Full thesis description.
        strategy: Investment strategy.
        status: Current thesis status.
        symbols: Associated ticker symbols.
        universe_keywords: Keywords for discovery.
        validation_criteria: Validation criteria.
        failure_criteria: Failure criteria.
        horizon: Investment horizon.
        conviction: Conviction level.
        source_module: Source module.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        positions_count: Number of linked positions.
        total_value: Total value of linked positions.
        unrealized_pnl: Unrealized P/L for linked positions.
        signals_pending: Number of pending signals.
        signals_approved: Number of approved signals.
        signals_rejected: Number of rejected signals.
    """

    id: int = Field(..., description="Thesis ID")
    title: str = Field(..., description="Thesis title")
    thesis_text: str = Field(..., description="Full thesis description")
    strategy: str = Field(..., description="Investment strategy")
    status: str = Field(..., description="Current status")
    symbols: list[str] = Field(..., description="Associated symbols")
    universe_keywords: list[str] = Field(..., description="Discovery keywords")
    validation_criteria: list[str] = Field(..., description="Validation criteria")
    failure_criteria: list[str] = Field(..., description="Failure criteria")
    horizon: str = Field(..., description="Investment horizon")
    conviction: float = Field(..., description="Conviction level")
    source_module: str = Field(..., description="Source module")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")
    positions_count: int = Field(..., description="Number of linked positions")
    total_value: float = Field(..., description="Total value of positions")
    unrealized_pnl: float = Field(..., description="Unrealized P/L")
    signals_pending: int = Field(..., description="Pending signals count")
    signals_approved: int = Field(..., description="Approved signals count")
    signals_rejected: int = Field(..., description="Rejected signals count")


@router.get("/theses", response_model=list[ThesisResponse])
async def list_theses(
    status: str | None = None, engines: Any = Depends(get_engines)
) -> list[ThesisResponse]:
    """List all theses with optional status filtering.

    Returns all theses with their current status, linked positions, and
    signal statistics. This endpoint powers the dashboard thesis panel.

    Args:
        status: Optional status filter ('active', 'confirmed', etc.).
        engines: Engine container with thesis engine and database.

    Returns:
        List of ThesisResponse models with current data.
    """
    try:
        # Build query with optional status filter
        where_clause = "WHERE status = ?" if status else ""
        params = (status,) if status else ()

        theses = engines.db.fetchall(
            f"""
            SELECT * FROM theses
            {where_clause}
            ORDER BY created_at DESC
        """,
            params,
        )

        result = []
        for thesis in theses:
            # Get linked positions
            positions = engines.db.fetchall(
                """
                SELECT symbol, shares, avg_cost, side
                FROM positions
                WHERE thesis_id = ? AND shares > 0
            """,
                (thesis["id"],),
            )

            # Calculate position metrics
            positions_count = len(positions)
            total_value = 0.0
            unrealized_pnl = 0.0

            for position in positions:
                try:
                    price_data = engines.pricing.get_price(position["symbol"])
                    current_price = price_data["price"] if price_data else position["avg_cost"]

                    market_value = position["shares"] * current_price
                    cost_basis = position["shares"] * position["avg_cost"]

                    if position["side"] == "long":
                        total_value += market_value
                        unrealized_pnl += market_value - cost_basis
                    else:  # short
                        total_value -= market_value
                        unrealized_pnl += cost_basis - market_value

                except Exception as e:
                    logger.warning(
                        "Failed to get price for %s in thesis %s: %s",
                        position["symbol"],
                        thesis["id"],
                        e,
                    )
                    market_value = position["shares"] * position["avg_cost"]
                    if position["side"] == "long":
                        total_value += market_value
                    else:
                        total_value -= market_value

            # Get signal statistics
            signals = engines.db.fetchone(
                """
                SELECT
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                FROM signals
                WHERE thesis_id = ?
            """,
                (thesis["id"],),
            )

            signals_pending = signals["pending"] or 0 if signals else 0
            signals_approved = signals["approved"] or 0 if signals else 0
            signals_rejected = signals["rejected"] or 0 if signals else 0

            # Parse JSON fields
            import json

            symbols = json.loads(thesis["symbols"]) if thesis["symbols"] else []
            universe_keywords = (
                json.loads(thesis["universe_keywords"]) if thesis["universe_keywords"] else []
            )
            validation_criteria = (
                json.loads(thesis["validation_criteria"]) if thesis["validation_criteria"] else []
            )
            failure_criteria = (
                json.loads(thesis["failure_criteria"]) if thesis["failure_criteria"] else []
            )

            result.append(
                ThesisResponse(
                    id=thesis["id"],
                    title=thesis["title"],
                    thesis_text=thesis["thesis_text"],
                    strategy=thesis["strategy"],
                    status=thesis["status"],
                    symbols=symbols,
                    universe_keywords=universe_keywords,
                    validation_criteria=validation_criteria,
                    failure_criteria=failure_criteria,
                    horizon=thesis["horizon"],
                    conviction=thesis["conviction"],
                    source_module=thesis["source_module"] or "manual",
                    created_at=thesis["created_at"],
                    updated_at=thesis["updated_at"],
                    positions_count=positions_count,
                    total_value=total_value,
                    unrealized_pnl=unrealized_pnl,
                    signals_pending=signals_pending,
                    signals_approved=signals_approved,
                    signals_rejected=signals_rejected,
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to list theses: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list theses: {str(e)}",
        )


@router.post("/theses", response_model=ThesisResponse)
async def create_thesis(
    thesis_request: ThesisRequest, engines: Any = Depends(get_engines)
) -> ThesisResponse:
    """Create a new investment thesis.

    Creates a new thesis using the ThesisEngine, which handles validation,
    ticker discovery, and initial signal generation. This endpoint is used
    by both money_thoughts integration and manual thesis entry.

    Args:
        thesis_request: Thesis data to create.
        engines: Engine container with thesis engine.

    Returns:
        ThesisResponse model for the created thesis.
    """
    try:
        from engine import Thesis as ThesisModel

        # Build Thesis model and persist via engine
        thesis_model = ThesisModel(
            title=thesis_request.title,
            thesis_text=thesis_request.thesis_text,
            strategy=thesis_request.strategy,
            symbols=thesis_request.symbols or [],
            universe_keywords=thesis_request.universe_keywords or [],
            validation_criteria=thesis_request.validation_criteria or [],
            failure_criteria=thesis_request.failure_criteria or [],
            horizon=thesis_request.horizon,
            conviction=thesis_request.conviction,
            source_module=thesis_request.source_module or "manual",
        )
        created = engines.thesis_engine.create_thesis(thesis_model)

        # Get the created thesis from DB for full response
        thesis = engines.db.fetchone(
            """
            SELECT * FROM theses WHERE id = ?
        """,
            (created.id,),
        )

        if not thesis:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created thesis",
            )

        # Parse JSON fields
        import json

        symbols = json.loads(thesis["symbols"]) if thesis["symbols"] else []
        universe_keywords = (
            json.loads(thesis["universe_keywords"]) if thesis["universe_keywords"] else []
        )
        validation_criteria = (
            json.loads(thesis["validation_criteria"]) if thesis["validation_criteria"] else []
        )
        failure_criteria = (
            json.loads(thesis["failure_criteria"]) if thesis["failure_criteria"] else []
        )

        # Return response (new thesis has no positions/signals yet)
        return ThesisResponse(
            id=thesis["id"],
            title=thesis["title"],
            thesis_text=thesis["thesis_text"],
            strategy=thesis["strategy"],
            status=thesis["status"],
            symbols=symbols,
            universe_keywords=universe_keywords,
            validation_criteria=validation_criteria,
            failure_criteria=failure_criteria,
            horizon=thesis["horizon"],
            conviction=thesis["conviction"],
            source_module=thesis["source_module"] or "manual",
            created_at=thesis["created_at"],
            updated_at=thesis["updated_at"],
            positions_count=0,
            total_value=0.0,
            unrealized_pnl=0.0,
            signals_pending=0,
            signals_approved=0,
            signals_rejected=0,
        )

    except Exception as e:
        logger.error("Failed to create thesis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create thesis: {str(e)}",
        )


@router.put("/theses/{thesis_id}", response_model=ThesisResponse)
async def update_thesis(
    thesis_id: int, thesis_update: ThesisUpdate, engines: Any = Depends(get_engines)
) -> ThesisResponse:
    """Update thesis status with reason and evidence.

    Updates a thesis status using the ThesisEngine, which handles state
    validation and automatic signal generation for status changes.

    Args:
        thesis_id: ID of the thesis to update.
        thesis_update: Update data including new status and reason.
        engines: Engine container with thesis engine.

    Returns:
        ThesisResponse model for the updated thesis.

    Raises:
        HTTPException: If thesis is not found.
    """
    try:
        # Check thesis exists
        thesis = engines.db.fetchone(
            """
            SELECT * FROM theses WHERE id = ?
        """,
            (thesis_id,),
        )

        if not thesis:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Thesis {thesis_id} not found"
            )

        # Use ThesisEngine to transition status
        from engine import ThesisStatus as TStat

        engines.thesis_engine.transition_status(
            thesis_id=thesis_id,
            new_status=TStat(thesis_update.status),
            reason=thesis_update.reason or "",
            evidence=thesis_update.evidence or "",
        )

        # Get the updated thesis with position/signal data
        # (Reuse the logic from list_theses for consistency)
        updated_thesis = engines.db.fetchone(
            """
            SELECT * FROM theses WHERE id = ?
        """,
            (thesis_id,),
        )

        # Get linked positions
        positions = engines.db.fetchall(
            """
            SELECT symbol, shares, avg_cost, side
            FROM positions
            WHERE thesis_id = ? AND shares > 0
        """,
            (thesis_id,),
        )

        # Calculate position metrics
        positions_count = len(positions)
        total_value = 0.0
        unrealized_pnl = 0.0

        for position in positions:
            try:
                price_data = engines.pricing.get_price(position["symbol"])
                current_price = price_data["price"] if price_data else position["avg_cost"]

                market_value = position["shares"] * current_price
                cost_basis = position["shares"] * position["avg_cost"]

                if position["side"] == "long":
                    total_value += market_value
                    unrealized_pnl += market_value - cost_basis
                else:  # short
                    total_value -= market_value
                    unrealized_pnl += cost_basis - market_value

            except Exception as e:
                logger.warning("Failed to get price for %s: %s", position["symbol"], e)
                market_value = position["shares"] * position["avg_cost"]
                if position["side"] == "long":
                    total_value += market_value
                else:
                    total_value -= market_value

        # Get signal statistics
        signals = engines.db.fetchone(
            """
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
            FROM signals
            WHERE thesis_id = ?
        """,
            (thesis_id,),
        )

        signals_pending = signals["pending"] or 0 if signals else 0
        signals_approved = signals["approved"] or 0 if signals else 0
        signals_rejected = signals["rejected"] or 0 if signals else 0

        # Parse JSON fields
        import json

        symbols = json.loads(updated_thesis["symbols"]) if updated_thesis["symbols"] else []
        universe_keywords = (
            json.loads(updated_thesis["universe_keywords"])
            if updated_thesis["universe_keywords"]
            else []
        )
        validation_criteria = (
            json.loads(updated_thesis["validation_criteria"])
            if updated_thesis["validation_criteria"]
            else []
        )
        failure_criteria = (
            json.loads(updated_thesis["failure_criteria"])
            if updated_thesis["failure_criteria"]
            else []
        )

        return ThesisResponse(
            id=updated_thesis["id"],
            title=updated_thesis["title"],
            thesis_text=updated_thesis["thesis_text"],
            strategy=updated_thesis["strategy"],
            status=updated_thesis["status"],
            symbols=symbols,
            universe_keywords=universe_keywords,
            validation_criteria=validation_criteria,
            failure_criteria=failure_criteria,
            horizon=updated_thesis["horizon"],
            conviction=updated_thesis["conviction"],
            source_module=updated_thesis["source_module"] or "manual",
            created_at=updated_thesis["created_at"],
            updated_at=updated_thesis["updated_at"],
            positions_count=positions_count,
            total_value=total_value,
            unrealized_pnl=unrealized_pnl,
            signals_pending=signals_pending,
            signals_approved=signals_approved,
            signals_rejected=signals_rejected,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update thesis %s: %s", thesis_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update thesis: {str(e)}",
        )
