"""Administrative API endpoints.

This module provides REST API endpoints for administrative functions including
kill switch control, mode switching, and audit log access. These endpoints
require elevated permissions and handle system-level operations.

Endpoints:
    POST /api/fund/kill-switch - Toggle kill switch on/off
    POST /api/fund/mode/{mode} - Switch between mock and live modes
    GET /api/fund/audit-log - Retrieve audit log entries

These endpoints are used for system management and emergency controls.

Dependencies:
    - Requires authenticated session via auth middleware
    - Uses RiskManager for kill switch operations
    - Uses database for audit log and mode management
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import get_engines
from config.settings import Mode

logger = logging.getLogger(__name__)

router = APIRouter()


class KillSwitchRequest(BaseModel):
    """Kill switch toggle request model.

    Attributes:
        active: Whether to activate or deactivate kill switch.
        reason: Reason for the action.
    """

    active: bool = Field(..., description="Activate or deactivate kill switch")
    reason: str = Field(..., min_length=5, description="Reason for action")


class KillSwitchResponse(BaseModel):
    """Kill switch status response model.

    Attributes:
        active: Current kill switch status.
        activated_at: Activation timestamp.
        activated_by: Who activated it.
        reason: Activation reason.
        message: Status message.
    """

    active: bool = Field(..., description="Kill switch status")
    activated_at: str | None = Field(None, description="Activation timestamp")
    activated_by: str | None = Field(None, description="Activated by")
    reason: str | None = Field(None, description="Activation reason")
    message: str = Field(..., description="Status message")


class ModeSwitch(BaseModel):
    """Mode switch request model.

    Attributes:
        confirmation: Required confirmation string.
        backup_data: Whether to backup current data before switch.
    """

    confirmation: str = Field(..., description="Confirmation string")
    backup_data: bool = Field(True, description="Backup data before switch")


class ModeSwitchResponse(BaseModel):
    """Mode switch response model.

    Attributes:
        old_mode: Previous mode.
        new_mode: New mode.
        switched_at: Switch timestamp.
        message: Status message.
        warnings: Any warnings about the switch.
    """

    old_mode: str = Field(..., description="Previous mode")
    new_mode: str = Field(..., description="New mode")
    switched_at: str = Field(..., description="Switch timestamp")
    message: str = Field(..., description="Status message")
    warnings: list[str] = Field(..., description="Switch warnings")


class AuditLogEntry(BaseModel):
    """Audit log entry response model.

    Attributes:
        id: Log entry ID.
        timestamp: Entry timestamp.
        action: Action performed.
        entity_type: Type of entity affected.
        entity_id: ID of entity affected.
        actor: Who performed the action.
        details: Additional details (JSON).
        ip_address: IP address (if available).
        user_agent: User agent (if available).
    """

    id: int = Field(..., description="Log entry ID")
    timestamp: str = Field(..., description="Timestamp")
    action: str = Field(..., description="Action performed")
    entity_type: str | None = Field(None, description="Entity type")
    entity_id: int | None = Field(None, description="Entity ID")
    actor: str = Field(..., description="Actor")
    details: str | None = Field(None, description="Additional details")
    ip_address: str | None = Field(None, description="IP address")
    user_agent: str | None = Field(None, description="User agent")


@router.post("/kill-switch", response_model=KillSwitchResponse)
async def toggle_kill_switch(
    request: KillSwitchRequest, engines: Any = Depends(get_engines)
) -> KillSwitchResponse:
    """Toggle the system kill switch on or off.

    The kill switch halts all new trading when activated, allowing only
    close-only orders. This is an emergency control for risk management.

    Args:
        request: Kill switch toggle request with reason.
        engines: Engine container with risk manager.

    Returns:
        KillSwitchResponse with current status.

    Raises:
        HTTPException: If kill switch operation fails.
    """
    try:
        # Get current kill switch status
        current_status = engines.db.fetchone("""
            SELECT * FROM kill_switch
            ORDER BY id DESC
            LIMIT 1
        """)

        current_active = current_status["active"] if current_status else False

        if request.active == current_active:
            # No change needed
            status_msg = "activated" if request.active else "deactivated"
            return KillSwitchResponse(
                active=current_active,
                activated_at=current_status.get("activated_at") if current_status else None,
                activated_by=current_status.get("activated_by") if current_status else None,
                reason=current_status.get("reason") if current_status else None,
                message=f"Kill switch already {status_msg}",
            )

        # Update kill switch status
        from datetime import UTC, datetime

        timestamp = datetime.now(UTC).isoformat() + "Z"

        if request.active:
            # Activate kill switch
            engines.db.execute(
                """
                INSERT INTO kill_switch (active, activated_at, reason, activated_by)
                VALUES (TRUE, ?, ?, 'api')
            """,
                (timestamp, request.reason),
            )

            message = "Kill switch ACTIVATED - All new trading halted"
            logger.warning("Kill switch activated: %s", request.reason)

        else:
            # Deactivate kill switch
            engines.db.execute(
                """
                UPDATE kill_switch
                SET active = FALSE, deactivated_at = ?
                WHERE active = TRUE
            """,
                (timestamp,),
            )

            message = "Kill switch deactivated - Trading resumed"
            logger.info("Kill switch deactivated: %s", request.reason)

        engines.db.connect().commit()

        # Log to audit trail
        engines.db.execute(
            """
            INSERT INTO audit_log (action, entity_type, entity_id, actor, details)
            VALUES (?, 'system', NULL, 'api', ?)
        """,
            (
                "kill_switch_activated" if request.active else "kill_switch_deactivated",
                f"Reason: {request.reason}",
            ),
        )
        engines.db.connect().commit()

        return KillSwitchResponse(
            active=request.active,
            activated_at=timestamp if request.active else None,
            activated_by="api" if request.active else None,
            reason=request.reason if request.active else None,
            message=message,
        )

    except Exception as e:
        logger.error("Failed to toggle kill switch: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle kill switch: {str(e)}",
        )


@router.get("/kill-switch/status", response_model=KillSwitchResponse)
async def get_kill_switch_status(engines: Any = Depends(get_engines)) -> KillSwitchResponse:
    """Get current kill switch status.

    Args:
        engines: Engine container with database.

    Returns:
        KillSwitchResponse with current status.
    """
    try:
        current_status = engines.db.fetchone("""
            SELECT * FROM kill_switch
            WHERE active = TRUE
            ORDER BY id DESC
            LIMIT 1
        """)

        if current_status:
            return KillSwitchResponse(
                active=True,
                activated_at=current_status["activated_at"],
                activated_by=current_status["activated_by"],
                reason=current_status["reason"],
                message="Kill switch is ACTIVE - Trading halted",
            )
        else:
            return KillSwitchResponse(
                active=False,
                activated_at=None,
                activated_by=None,
                reason=None,
                message="Kill switch is inactive - Trading allowed",
            )

    except Exception as e:
        logger.error("Failed to get kill switch status: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get kill switch status: {str(e)}",
        )


@router.post("/mode/{mode}", response_model=ModeSwitchResponse)
async def switch_mode(
    mode: Mode, switch_request: ModeSwitch, engines: Any = Depends(get_engines)
) -> ModeSwitchResponse:
    """Switch between mock and live trading modes.

    DANGEROUS OPERATION: Switching to live mode enables real money trading.
    Requires explicit confirmation and optionally creates data backup.

    Args:
        mode: Target mode (mock or live).
        switch_request: Switch confirmation and options.
        engines: Engine container.

    Returns:
        ModeSwitchResponse with switch results.

    Raises:
        HTTPException: If confirmation is invalid or switch fails.
    """
    try:
        from config.settings import get_settings

        current_settings = get_settings()
        current_mode = current_settings.mode

        if mode == current_mode:
            return ModeSwitchResponse(
                old_mode=current_mode,
                new_mode=mode,
                switched_at="",
                message=f"Already in {mode} mode",
                warnings=[],
            )

        # Validate confirmation for live mode switch
        if mode == Mode.LIVE:
            required_confirmation = "SWITCH_TO_LIVE_MODE_WITH_REAL_MONEY"
            if switch_request.confirmation != required_confirmation:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid confirmation. Required: {required_confirmation}",
                )

        # Collect warnings
        warnings = []

        if mode == Mode.LIVE:
            warnings.extend(
                [
                    "LIVE MODE USES REAL MONEY",
                    "All trades will be executed via Schwab API",
                    "Ensure sufficient account balance and risk limits",
                    "Monitor positions closely after switch",
                ]
            )

            # Check if Schwab credentials are configured
            if not current_settings.schwab_app_key or not current_settings.schwab_secret:
                warnings.append("WARNING: Schwab API credentials not configured")

        # Note: Actual mode switching would require application restart
        # or dynamic configuration reloading. For now, just log the intent.

        from datetime import UTC, datetime

        timestamp = datetime.now(UTC).isoformat() + "Z"

        # Log the mode switch request
        engines.db.execute(
            """
            INSERT INTO audit_log (action, entity_type, entity_id, actor, details)
            VALUES ('mode_switch_requested', 'system', NULL, 'api', ?)
        """,
            (f"From {current_mode} to {mode}. Confirmation: {switch_request.confirmation}",),
        )
        engines.db.connect().commit()

        logger.critical(
            "MODE SWITCH REQUESTED: %s -> %s (confirmation: %s)",
            current_mode,
            mode,
            switch_request.confirmation,
        )

        # In a real implementation, this would:
        # 1. Backup current database if requested
        # 2. Update configuration
        # 3. Restart application with new mode
        # 4. Initialize appropriate broker instance

        message = (
            f"Mode switch from {current_mode} to {mode} logged. Restart required to take effect."
        )

        return ModeSwitchResponse(
            old_mode=current_mode,
            new_mode=mode,
            switched_at=timestamp,
            message=message,
            warnings=warnings,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to switch mode: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to switch mode: {str(e)}",
        )


@router.get("/audit-log", response_model=list[AuditLogEntry])
async def get_audit_log(
    limit: int = Query(100, ge=1, le=1000, description="Maximum entries to return"),
    action: str | None = Query(None, description="Filter by action type"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    actor: str | None = Query(None, description="Filter by actor"),
    days: int = Query(7, ge=1, le=90, description="Days to look back"),
    engines: Any = Depends(get_engines),
) -> list[AuditLogEntry]:
    """Retrieve audit log entries with filtering options.

    Returns system audit log for compliance, debugging, and monitoring.
    Entries are ordered by timestamp (newest first).

    Args:
        limit: Maximum number of entries to return.
        action: Optional action filter.
        entity_type: Optional entity type filter.
        actor: Optional actor filter.
        days: Number of days to look back.
        engines: Engine container with database.

    Returns:
        List of AuditLogEntry models.
    """
    try:
        # Build WHERE clause with filters
        where_conditions = [f"timestamp >= datetime('now', '-{days} days')"]
        params = []

        if action:
            where_conditions.append("action = ?")
            params.append(action)

        if entity_type:
            where_conditions.append("entity_type = ?")
            params.append(entity_type)

        if actor:
            where_conditions.append("actor = ?")
            params.append(actor)

        where_clause = "WHERE " + " AND ".join(where_conditions)
        params.append(limit)

        # Query audit log
        audit_entries = engines.db.fetchall(
            f"""
            SELECT * FROM audit_log
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            params,
        )

        result = []
        for entry in audit_entries:
            result.append(
                AuditLogEntry(
                    id=entry["id"],
                    timestamp=entry["timestamp"],
                    action=entry["action"],
                    entity_type=entry["entity_type"],
                    entity_id=entry["entity_id"],
                    actor=entry["actor"],
                    details=entry["details"],
                    ip_address=entry.get("ip_address"),  # May not exist in current schema
                    user_agent=entry.get("user_agent"),  # May not exist in current schema
                )
            )

        return result

    except Exception as e:
        logger.error("Failed to get audit log: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get audit log: {str(e)}",
        )
