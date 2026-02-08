"""WebSocket endpoint for real-time price streaming.

This module provides a WebSocket endpoint at /ws/prices that streams real-time
price updates to connected dashboard clients. The WebSocket connection is
authenticated using the same session cookies as the REST API.

Price updates are broadcast to all connected clients when prices change for
any symbol in the current portfolio. The pricing service handles the actual
price fetching and caching, while this module manages WebSocket connections
and message broadcasting.

Message format:
```json
{
    "type": "price_update",
    "symbol": "NVDA",
    "price": 129.50,
    "change_pct": 2.3,
    "timestamp": "2026-02-07T15:30:00Z"
}
```

Functions:
    create_websocket_router: Creates the FastAPI router with WebSocket endpoint.
    authenticate_websocket: Validates session cookie for WebSocket connections.
    broadcast_price_update: Sends price updates to all connected clients.

Dependencies:
    - WebSocket connections are authenticated using session cookies
    - Requires access to the PricingService via dependency injection
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from api.deps import get_engines
from config.settings import get_settings

logger = logging.getLogger(__name__)

# Global set to track active WebSocket connections
_active_connections: set[WebSocket] = set()


def create_websocket_router() -> APIRouter:
    """Create the FastAPI router with WebSocket price streaming endpoint.

    Returns:
        FastAPI router with /ws/prices WebSocket endpoint.
    """
    router = APIRouter()

    @router.websocket("/ws/prices")
    async def websocket_prices(websocket: WebSocket, engines: Any = Depends(get_engines)) -> None:
        """WebSocket endpoint for real-time price streaming.

        Authenticates the WebSocket connection using session cookies, then
        maintains a persistent connection to stream price updates. Clients
        receive JSON messages when prices change for any portfolio position.

        Args:
            websocket: FastAPI WebSocket connection.
            engines: Engine container with pricing service and database.

        Side effects:
            - Adds connection to global active connections set
            - Sends price updates to all connected clients
            - Removes connection on disconnect
        """
        # Authenticate WebSocket connection
        if not await authenticate_websocket(websocket):
            logger.warning("WebSocket authentication failed")
            await websocket.close(code=4001, reason="Authentication failed")
            return

        await websocket.accept()
        logger.info("WebSocket connection established")

        try:
            # Add to active connections
            _active_connections.add(websocket)

            # Send initial price snapshot for all portfolio positions
            await send_initial_prices(websocket, engines)

            # Keep connection alive and handle incoming messages
            while True:
                # Wait for client messages (currently just ping/pong)
                try:
                    data = await websocket.receive_text()
                    # Echo back for ping/pong testing
                    if data == "ping":
                        await websocket.send_text("pong")
                except WebSocketDisconnect:
                    break

        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected")
        except Exception as e:
            logger.error("WebSocket error: %s", e)
            await websocket.close(code=1011, reason="Internal error")
        finally:
            # Remove from active connections
            _active_connections.discard(websocket)

    return router


async def authenticate_websocket(websocket: WebSocket) -> bool:
    """Authenticate WebSocket connection using session cookie.

    Validates the session cookie sent with the WebSocket handshake request.
    Uses the same authentication logic as the REST API middleware.

    Args:
        websocket: FastAPI WebSocket connection with headers and cookies.

    Returns:
        True if authentication succeeds, False otherwise.
    """
    settings = get_settings()
    serializer = URLSafeTimedSerializer(settings.session_secret_key)

    # Extract session cookie from WebSocket headers
    cookies = websocket.headers.get("cookie", "")
    session_cookie = None

    for cookie in cookies.split(";"):
        if "session=" in cookie:
            session_cookie = cookie.split("session=")[1].strip()
            break

    if not session_cookie:
        logger.warning("No session cookie in WebSocket handshake")
        return False

    try:
        # Validate session cookie (max age: 7 days)
        serializer.loads(session_cookie, max_age=7 * 24 * 60 * 60)
        return True
    except (BadSignature, SignatureExpired, KeyError):
        logger.warning("Invalid or expired session in WebSocket handshake")
        return False


async def send_initial_prices(websocket: WebSocket, engines: Any) -> None:
    """Send initial price snapshot for all portfolio positions.

    Queries the database for all current positions and sends their current
    prices to the newly connected WebSocket client.

    Args:
        websocket: WebSocket connection to send prices to.
        engines: Engine container with database and pricing service.
    """
    try:
        # Get all position symbols
        positions = engines.db.fetchall("SELECT DISTINCT symbol FROM positions WHERE shares > 0")

        for position in positions:
            symbol = position["symbol"]
            try:
                # Get current price
                price_data = engines.pricing.get_price(symbol)
                if price_data:
                    message = {
                        "type": "price_update",
                        "symbol": symbol,
                        "price": price_data["price"],
                        "change_pct": price_data.get("change_pct", 0.0),
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    await websocket.send_text(json.dumps(message))

            except Exception as e:
                logger.error("Failed to send initial price for %s: %s", symbol, e)

    except Exception as e:
        logger.error("Failed to send initial prices: %s", e)


async def broadcast_price_update(symbol: str, price: float, change_pct: float = 0.0) -> None:
    """Broadcast price update to all connected WebSocket clients.

    This function is called by the pricing service when prices are updated
    to notify all dashboard clients of the new prices.

    Args:
        symbol: Stock symbol that was updated.
        price: New price value.
        change_pct: Percentage change from previous price.

    Side effects:
        - Sends price update message to all active WebSocket connections
        - Removes any dead connections from the active set
    """
    if not _active_connections:
        return

    message = {
        "type": "price_update",
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    message_text = json.dumps(message)

    # Send to all active connections
    dead_connections: set[WebSocket] = set()

    for connection in _active_connections:
        try:
            await connection.send_text(message_text)
        except Exception as e:
            logger.warning("Failed to send price update to client: %s", e)
            dead_connections.add(connection)

    # Remove dead connections
    for dead_connection in dead_connections:
        _active_connections.discard(dead_connection)

    if dead_connections:
        logger.info("Removed %d dead WebSocket connections", len(dead_connections))


def get_active_connection_count() -> int:
    """Get the number of active WebSocket connections.

    Used for monitoring and debugging purposes.

    Returns:
        Number of currently active WebSocket connections.
    """
    return len(_active_connections)
