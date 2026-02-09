"""Watchlist trigger management API endpoints.

Provides CRUD endpoints for watchlist triggers that define price-based
conditions (entry, exit, stop_loss, take_profit) linked to investment
theses. Triggers feed into the signal generator for automated alerts.

Endpoints:
    GET    /api/fund/watchlist        - List all active triggers
    POST   /api/fund/watchlist        - Create a new trigger
    PUT    /api/fund/watchlist/{id}   - Update a trigger
    DELETE /api/fund/watchlist/{id}   - Delete a trigger
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class WatchlistTriggerCreate(BaseModel):
    """Request model for creating a watchlist trigger.

    Attributes:
        thesis_id: Optional thesis this trigger belongs to.
        symbol: Ticker symbol to watch.
        trigger_type: Type of trigger (entry/exit/stop_loss/take_profit).
        condition: Condition operator (price_below/price_above/pct_change).
        target_value: Target price or percentage value.
        notes: Optional notes about the trigger.
    """

    thesis_id: int | None = Field(None, description="Linked thesis ID")
    symbol: str = Field(
        ..., min_length=1, max_length=10, description="Ticker symbol"
    )
    trigger_type: str = Field(
        ...,
        pattern="^(entry|exit|stop_loss|take_profit)$",
        description="Trigger type",
    )
    condition: str = Field(
        ...,
        pattern="^(price_below|price_above|pct_change)$",
        description="Condition operator",
    )
    target_value: float = Field(..., description="Target value")
    notes: str | None = Field(None, description="Optional notes")


class WatchlistTriggerUpdate(BaseModel):
    """Request model for updating a watchlist trigger.

    All fields are optional; only provided fields are updated.

    Attributes:
        symbol: Ticker symbol to watch.
        trigger_type: Type of trigger.
        condition: Condition operator.
        target_value: Target price or percentage value.
        notes: Optional notes.
        active: Whether trigger is active.
    """

    symbol: str | None = Field(None, max_length=10, description="Ticker")
    trigger_type: str | None = Field(
        None,
        pattern="^(entry|exit|stop_loss|take_profit)$",
        description="Trigger type",
    )
    condition: str | None = Field(
        None,
        pattern="^(price_below|price_above|pct_change)$",
        description="Condition",
    )
    target_value: float | None = Field(None, description="Target value")
    notes: str | None = Field(None, description="Notes")
    active: int | None = Field(None, ge=0, le=1, description="Active flag")


class WatchlistTriggerResponse(BaseModel):
    """Response model for a watchlist trigger.

    Attributes:
        id: Trigger database ID.
        thesis_id: Linked thesis ID (nullable).
        thesis_title: Linked thesis title (nullable).
        symbol: Ticker symbol.
        trigger_type: Type of trigger.
        condition: Condition operator.
        target_value: Target value.
        notes: Optional notes.
        active: Whether trigger is active (1/0).
        created_at: Creation timestamp.
        triggered_at: When trigger fired (nullable).
    """

    id: int
    thesis_id: int | None = None
    thesis_title: str | None = None
    symbol: str
    trigger_type: str
    condition: str
    target_value: float
    notes: str | None = None
    active: int = 1
    created_at: str | None = None
    triggered_at: str | None = None


def _ensure_table(engines: Any) -> None:
    """Create watchlist_triggers table if it doesn't exist yet."""
    engines.db.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_id INTEGER REFERENCES theses(id),
            symbol TEXT NOT NULL,
            trigger_type TEXT NOT NULL
                CHECK(trigger_type IN
                    ('entry','exit','stop_loss','take_profit')),
            condition TEXT NOT NULL,
            target_value REAL NOT NULL,
            notes TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            triggered_at TEXT,
            FOREIGN KEY (thesis_id) REFERENCES theses(id)
        )
    """)
    engines.db.connect().commit()


def _row_to_response(row: dict) -> WatchlistTriggerResponse:
    """Convert a database row dict to a WatchlistTriggerResponse."""
    return WatchlistTriggerResponse(
        id=row["id"],
        thesis_id=row.get("thesis_id"),
        thesis_title=row.get("thesis_title"),
        symbol=row["symbol"],
        trigger_type=row["trigger_type"],
        condition=row["condition"],
        target_value=row["target_value"],
        notes=row.get("notes"),
        active=row.get("active", 1),
        created_at=row.get("created_at"),
        triggered_at=row.get("triggered_at"),
    )


@router.get(
    "/watchlist", response_model=list[WatchlistTriggerResponse]
)
async def list_triggers(
    active_only: bool = True,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> list[WatchlistTriggerResponse]:
    """List watchlist triggers, optionally filtered to active only.

    Args:
        active_only: If True, return only active triggers.
        engines: Engine container with database.
        user: Authenticated user.

    Returns:
        List of WatchlistTriggerResponse models.
    """
    _ensure_table(engines)
    where = "WHERE wt.active = 1" if active_only else ""
    rows = engines.db.fetchall(f"""
        SELECT wt.*, t.title AS thesis_title
        FROM watchlist_triggers wt
        LEFT JOIN theses t ON wt.thesis_id = t.id
        {where}
        ORDER BY wt.created_at DESC
    """)
    return [_row_to_response(r) for r in rows]


@router.post(
    "/watchlist", response_model=WatchlistTriggerResponse
)
async def create_trigger(
    body: WatchlistTriggerCreate,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> WatchlistTriggerResponse:
    """Create a new watchlist trigger.

    Args:
        body: Trigger creation data.
        engines: Engine container with database.
        user: Authenticated user.

    Returns:
        The created WatchlistTriggerResponse.

    Raises:
        HTTPException: If the linked thesis doesn't exist.
    """
    _ensure_table(engines)

    if body.thesis_id is not None:
        thesis = engines.db.fetchone(
            "SELECT id FROM theses WHERE id = ?", (body.thesis_id,)
        )
        if not thesis:
            raise HTTPException(
                status_code=404,
                detail=f"Thesis {body.thesis_id} not found",
            )

    engines.db.execute(
        """INSERT INTO watchlist_triggers
           (thesis_id, symbol, trigger_type, condition,
            target_value, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            body.thesis_id,
            body.symbol.upper(),
            body.trigger_type,
            body.condition,
            body.target_value,
            body.notes,
        ),
    )
    engines.db.connect().commit()

    row_id = engines.db.fetchone(
        "SELECT last_insert_rowid() AS id"
    )["id"]
    row = engines.db.fetchone(
        """SELECT wt.*, t.title AS thesis_title
           FROM watchlist_triggers wt
           LEFT JOIN theses t ON wt.thesis_id = t.id
           WHERE wt.id = ?""",
        (row_id,),
    )
    return _row_to_response(row)


@router.put(
    "/watchlist/{trigger_id}",
    response_model=WatchlistTriggerResponse,
)
async def update_trigger(
    trigger_id: int,
    body: WatchlistTriggerUpdate,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> WatchlistTriggerResponse:
    """Update a watchlist trigger by ID.

    Only provided (non-None) fields are updated.

    Args:
        trigger_id: ID of the trigger to update.
        body: Fields to update.
        engines: Engine container with database.
        user: Authenticated user.

    Returns:
        The updated WatchlistTriggerResponse.

    Raises:
        HTTPException: If trigger not found or no fields to update.
    """
    _ensure_table(engines)

    existing = engines.db.fetchone(
        "SELECT id FROM watchlist_triggers WHERE id = ?",
        (trigger_id,),
    )
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=f"Trigger {trigger_id} not found",
        )

    updates: dict[str, Any] = {}
    if body.symbol is not None:
        updates["symbol"] = body.symbol.upper()
    if body.trigger_type is not None:
        updates["trigger_type"] = body.trigger_type
    if body.condition is not None:
        updates["condition"] = body.condition
    if body.target_value is not None:
        updates["target_value"] = body.target_value
    if body.notes is not None:
        updates["notes"] = body.notes
    if body.active is not None:
        updates["active"] = body.active

    if not updates:
        raise HTTPException(
            status_code=400, detail="No fields to update"
        )

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [trigger_id]
    engines.db.execute(
        f"UPDATE watchlist_triggers SET {set_clause} WHERE id = ?",
        tuple(values),
    )
    engines.db.connect().commit()

    row = engines.db.fetchone(
        """SELECT wt.*, t.title AS thesis_title
           FROM watchlist_triggers wt
           LEFT JOIN theses t ON wt.thesis_id = t.id
           WHERE wt.id = ?""",
        (trigger_id,),
    )
    return _row_to_response(row)


@router.delete("/watchlist/{trigger_id}")
async def delete_trigger(
    trigger_id: int,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a watchlist trigger by ID.

    Args:
        trigger_id: ID of the trigger to delete.
        engines: Engine container with database.
        user: Authenticated user.

    Returns:
        Confirmation dict with deleted trigger ID.

    Raises:
        HTTPException: If trigger not found.
    """
    _ensure_table(engines)

    existing = engines.db.fetchone(
        "SELECT id FROM watchlist_triggers WHERE id = ?",
        (trigger_id,),
    )
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=f"Trigger {trigger_id} not found",
        )

    engines.db.execute(
        "DELETE FROM watchlist_triggers WHERE id = ?",
        (trigger_id,),
    )
    engines.db.connect().commit()

    return {"status": "deleted", "id": trigger_id}
