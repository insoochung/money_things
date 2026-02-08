"""User profile and settings API endpoints.

Endpoints:
    GET  /api/fund/users/me              — Current user profile
    PUT  /api/fund/users/me              — Update user settings
    GET  /api/fund/users/me/telegram-link — Generate Telegram link code

Dependencies:
    - Requires authenticated session via get_current_user
"""

from __future__ import annotations

import logging
import random
import string
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.deps import get_engines

logger = logging.getLogger(__name__)

router = APIRouter()


class UserSettingsUpdate(BaseModel):
    """Request model for updating user settings.

    Attributes:
        name: Updated display name (optional).
        settings: JSON settings blob (timezone, notifications, etc.).
    """

    name: str | None = Field(None, description="Display name")
    settings: dict | None = Field(None, description="User settings JSON")


@router.get("/users/me")
async def get_me(user: dict = Depends(get_current_user)) -> dict:
    """Get current user profile."""
    return user


@router.put("/users/me")
async def update_me(
    update: UserSettingsUpdate,
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> dict:
    """Update current user settings."""
    try:
        import json

        if update.name:
            engines.db.execute(
                "UPDATE users SET name = ? WHERE id = ?",
                (update.name, user["id"]),
            )
        if update.settings is not None:
            engines.db.execute(
                "UPDATE users SET settings = ? WHERE id = ?",
                (json.dumps(update.settings), user["id"]),
            )
        engines.db.connect().commit()

        # Return updated user
        row = engines.db.fetchone(
            "SELECT id, email, name, role, settings FROM users WHERE id = ?",
            (user["id"],),
        )
        if row:
            result = dict(row)
            if result.get("settings"):
                result["settings"] = json.loads(result["settings"])
            return result
        return user
    except Exception as e:
        logger.error("Failed to update user settings: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/me/telegram-link")
async def get_telegram_link(
    engines: Any = Depends(get_engines),
    user: dict = Depends(get_current_user),
) -> dict:
    """Generate a 6-digit Telegram link code.

    The code expires after 10 minutes. The user sends /link <code> to the
    Telegram bot to associate their Telegram account with their Money Moves user.
    """
    try:
        code = "".join(random.choices(string.digits, k=6))

        # Store the link code (reuse audit_log or a dedicated table)
        engines.db.execute(
            """INSERT INTO audit_log (action, entity_type, entity_id, actor, details)
               VALUES ('telegram_link_code', 'user', ?, ?, ?)""",
            (user["id"], f"user:{user['id']}", f"code:{code}"),
        )
        engines.db.connect().commit()

        return {
            "code": code,
            "expires_in_minutes": 10,
            "instruction": "Send /link {code} to the Money Moves Telegram bot",
        }
    except Exception as e:
        logger.error("Failed to generate telegram link: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
