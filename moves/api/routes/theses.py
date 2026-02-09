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

from api.auth import get_current_user
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


class ThesisFieldUpdate(BaseModel):
    """Request model for editing thesis fields inline.

    All fields are optional; only provided fields are updated.

    Attributes:
        title: Updated thesis title.
        thesis_text: Updated thesis description.
        conviction: Updated conviction level (0.0 to 1.0).
        status: Updated status.
        symbols: Updated list of ticker symbols.
        strategy: Updated strategy.
        horizon: Updated investment horizon.
    """

    title: str | None = Field(None, max_length=200, description="Title")
    thesis_text: str | None = Field(None, description="Description")
    conviction: float | None = Field(
        None, ge=0.0, le=1.0, description="Conviction"
    )
    status: str | None = Field(
        None,
        pattern=(
            "^(draft|active|monitoring|archived|"
            "strengthening|confirmed|weakening|invalidated)$"
        ),
        description="Status",
    )
    symbols: list[str] | None = Field(None, description="Symbols")
    strategy: str | None = Field(
        None,
        pattern="^(long|short|long_short)$",
        description="Strategy",
    )
    horizon: str | None = Field(
        None,
        pattern="^(1d|1w|1m|3m|6m|1y)$",
        description="Horizon",
    )


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
    status: str | None = None,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
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
    thesis_request: ThesisRequest,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
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


@router.patch("/theses/{thesis_id}", response_model=ThesisResponse)
async def edit_thesis_fields(
    thesis_id: int,
    body: ThesisFieldUpdate,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> ThesisResponse:
    """Edit thesis fields inline (title, text, conviction, etc.).

    Only provided (non-None) fields are updated. This is the endpoint
    used by the dashboard inline editor, distinct from the status
    transition PUT endpoint.

    Args:
        thesis_id: ID of the thesis to edit.
        body: Fields to update.
        engines: Engine container with database.
        user: Authenticated user.

    Returns:
        ThesisResponse with updated data.

    Raises:
        HTTPException: If thesis not found or no fields provided.
    """
    import json

    thesis = engines.db.fetchone(
        "SELECT * FROM theses WHERE id = ?", (thesis_id,)
    )
    if not thesis:
        raise HTTPException(
            status_code=404, detail=f"Thesis {thesis_id} not found"
        )

    updates: dict[str, Any] = {}
    if body.title is not None:
        updates["title"] = body.title
    if body.thesis_text is not None:
        updates["thesis_text"] = body.thesis_text
    if body.conviction is not None:
        updates["conviction"] = body.conviction
    if body.status is not None:
        updates["status"] = body.status
    if body.symbols is not None:
        updates["symbols"] = json.dumps(body.symbols)
    if body.strategy is not None:
        updates["strategy"] = body.strategy
    if body.horizon is not None:
        updates["horizon"] = body.horizon

    if not updates:
        raise HTTPException(
            status_code=400, detail="No fields to update"
        )

    updates["updated_at"] = "datetime('now')"
    set_parts = []
    values = []
    for k, v in updates.items():
        if k == "updated_at":
            set_parts.append(f"{k} = datetime('now')")
        else:
            set_parts.append(f"{k} = ?")
            values.append(v)
    values.append(thesis_id)

    engines.db.execute(
        f"UPDATE theses SET {', '.join(set_parts)} WHERE id = ?",
        tuple(values),
    )
    engines.db.connect().commit()

    # Fetch updated thesis and build response
    updated = engines.db.fetchone(
        "SELECT * FROM theses WHERE id = ?", (thesis_id,)
    )
    symbols = json.loads(updated["symbols"]) if updated["symbols"] else []
    universe_keywords = (
        json.loads(updated["universe_keywords"])
        if updated["universe_keywords"]
        else []
    )
    validation_criteria = (
        json.loads(updated["validation_criteria"])
        if updated["validation_criteria"]
        else []
    )
    failure_criteria = (
        json.loads(updated["failure_criteria"])
        if updated["failure_criteria"]
        else []
    )

    return ThesisResponse(
        id=updated["id"],
        title=updated["title"],
        thesis_text=updated["thesis_text"],
        strategy=updated["strategy"],
        status=updated["status"],
        symbols=symbols,
        universe_keywords=universe_keywords,
        validation_criteria=validation_criteria,
        failure_criteria=failure_criteria,
        horizon=updated["horizon"],
        conviction=updated["conviction"],
        source_module=updated["source_module"] or "manual",
        created_at=updated["created_at"],
        updated_at=updated["updated_at"],
        positions_count=0,
        total_value=0.0,
        unrealized_pnl=0.0,
        signals_pending=0,
        signals_approved=0,
        signals_rejected=0,
    )


@router.put("/theses/{thesis_id}", response_model=ThesisResponse)
async def update_thesis(
    thesis_id: int,
    thesis_update: ThesisUpdate,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
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


# ── Thesis Sharing ──────────────────────────────────────────────


@router.post("/theses/{thesis_id}/share")
async def share_thesis(
    thesis_id: int, engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> dict:
    """Share a thesis for others to clone.

    Creates a shared_theses record so other users can discover and clone
    this thesis into their own portfolio.

    Args:
        thesis_id: ID of the thesis to share.
        engines: Engine container with database.
        user: Authenticated user.

    Returns:
        Confirmation with share details.
    """
    try:
        thesis = engines.db.fetchone(
            "SELECT * FROM theses WHERE id = ? AND user_id = ?",
            (thesis_id, user["id"]),
        )
        if not thesis:
            raise HTTPException(status_code=404, detail="Thesis not found or not owned by you")

        # Check if already shared
        existing = engines.db.fetchone(
            "SELECT id FROM shared_theses WHERE thesis_id = ? AND active = 1",
            (thesis_id,),
        )
        if existing:
            return {"status": "already_shared", "thesis_id": thesis_id}

        engines.db.execute(
            "INSERT INTO shared_theses (thesis_id, shared_by) VALUES (?, ?)",
            (thesis_id, user["id"]),
        )
        engines.db.connect().commit()
        return {"status": "shared", "thesis_id": thesis_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to share thesis %s: %s", thesis_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/theses/{thesis_id}/share")
async def unshare_thesis(
    thesis_id: int, engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> dict:
    """Unshare a thesis."""
    try:
        engines.db.execute(
            """UPDATE shared_theses SET active = 0
               WHERE thesis_id = ? AND shared_by = ? AND active = 1""",
            (thesis_id, user["id"]),
        )
        engines.db.connect().commit()
        return {"status": "unshared", "thesis_id": thesis_id}
    except Exception as e:
        logger.error("Failed to unshare thesis %s: %s", thesis_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shared-theses")
async def list_shared_theses(
    engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> list[dict]:
    """Browse theses shared by other users."""
    try:
        rows = engines.db.fetchall(
            """SELECT st.id as share_id, st.shared_at, st.thesis_id,
                      t.title, t.thesis_text, t.strategy, t.symbols, t.conviction, t.horizon,
                      u.name as shared_by_name
               FROM shared_theses st
               JOIN theses t ON st.thesis_id = t.id
               JOIN users u ON st.shared_by = u.id
               WHERE st.active = 1 AND st.shared_by != ?
               ORDER BY st.shared_at DESC""",
            (user["id"],),
        )
        import json

        result = []
        for row in rows:
            result.append(
                {
                    "share_id": row["share_id"],
                    "thesis_id": row["thesis_id"],
                    "title": row["title"],
                    "thesis_text": row["thesis_text"],
                    "strategy": row["strategy"],
                    "symbols": json.loads(row["symbols"]) if row["symbols"] else [],
                    "conviction": row["conviction"],
                    "horizon": row["horizon"],
                    "shared_by": row["shared_by_name"],
                    "shared_at": row["shared_at"],
                }
            )
        return result
    except Exception as e:
        logger.error("Failed to list shared theses: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/shared-theses/{thesis_id}/clone")
async def clone_thesis(
    thesis_id: int, engines: Any = Depends(get_engines), user: dict = Depends(get_current_user)
) -> dict:
    """Clone a shared thesis into your portfolio.

    Creates a new thesis owned by the current user, copying all fields from
    the shared thesis. The clone tracks its lineage via cloned_from.
    """
    try:
        # Verify it's actually shared
        shared = engines.db.fetchone(
            """SELECT t.* FROM shared_theses st
               JOIN theses t ON st.thesis_id = t.id
               WHERE st.thesis_id = ? AND st.active = 1""",
            (thesis_id,),
        )
        if not shared:
            raise HTTPException(status_code=404, detail="Shared thesis not found")

        # Clone: insert a new thesis for this user
        engines.db.execute(
            """INSERT INTO theses (title, thesis_text, strategy, symbols, universe_keywords,
                   validation_criteria, failure_criteria, horizon, conviction, source_module,
                   user_id, cloned_from, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                shared["title"],
                shared["thesis_text"],
                shared["strategy"],
                shared["symbols"],
                shared["universe_keywords"],
                shared["validation_criteria"],
                shared["failure_criteria"],
                shared["horizon"],
                shared["conviction"],
                shared["source_module"],
                user["id"],
                thesis_id,
            ),
        )
        engines.db.connect().commit()

        new_id = engines.db.fetchone("SELECT last_insert_rowid() as id")["id"]
        return {"status": "cloned", "new_thesis_id": new_id, "cloned_from": thesis_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to clone thesis %s: %s", thesis_id, e)
        raise HTTPException(status_code=500, detail=str(e))
