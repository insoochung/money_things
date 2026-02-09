"""Outcome tracking API endpoints.

Provides thesis performance scorecards — comparing conviction levels
against actual market returns to close the feedback loop.

Endpoints:
    GET /api/fund/outcomes - Score all active theses
    GET /api/fund/outcomes/{thesis_id} - Score a single thesis
    GET /api/fund/outcomes/{thesis_id}/history - Historical snapshots
    POST /api/fund/outcomes/snapshot - Persist current scorecards
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.deps import get_engines
from engine.outcome_tracker import OutcomeTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund/outcomes", tags=["outcomes"])


def _get_tracker(engines: Any) -> OutcomeTracker:
    return OutcomeTracker(engines.db)


@router.get("")
async def get_all_outcomes(
    fetch_prices: bool = Query(True, description="Fetch live prices"),
    user: dict = Depends(get_current_user),
    engines: Any = Depends(get_engines),
) -> dict[str, Any]:
    """Score all active theses against actual returns."""
    tracker = _get_tracker(engines)
    scorecards = tracker.score_all(fetch_prices=fetch_prices)
    return {
        "scorecards": [sc.to_dict() for sc in scorecards],
        "summary": tracker.format_summary(scorecards),
    }


@router.get("/{thesis_id}")
async def get_thesis_outcome(
    thesis_id: int,
    fetch_prices: bool = Query(True),
    user: dict = Depends(get_current_user),
    engines: Any = Depends(get_engines),
) -> dict[str, Any]:
    """Score a single thesis against actual returns."""
    tracker = _get_tracker(engines)
    sc = tracker.score_thesis(thesis_id, fetch_prices=fetch_prices)
    if not sc:
        raise HTTPException(status_code=404, detail="Thesis not found")
    return sc.to_dict()


@router.get("/{thesis_id}/history")
async def get_outcome_history(
    thesis_id: int,
    limit: int = Query(30, ge=1, le=365),
    user: dict = Depends(get_current_user),
    engines: Any = Depends(get_engines),
) -> list[dict[str, Any]]:
    """Get historical outcome snapshots for a thesis."""
    tracker = _get_tracker(engines)
    return tracker.get_history(thesis_id, limit=limit)


@router.post("/snapshot")
async def persist_snapshots(
    user: dict = Depends(get_current_user),
    engines: Any = Depends(get_engines),
) -> dict[str, Any]:
    """Persist current scorecards as daily snapshots."""
    tracker = _get_tracker(engines)
    scorecards = tracker.score_all(fetch_prices=True)
    saved = tracker.persist_all(scorecards)
    return {"saved": saved, "total": len(scorecards)}
