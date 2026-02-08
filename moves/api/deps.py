"""Dependency injection for the money_moves API.

This module provides the shared engine container and dependency function used
across all route modules. Extracted from app.py to avoid circular imports
between the application factory and route modules.

Classes:
    EngineContainer: Holds all initialized engine instances for the application.

Functions:
    get_engines: FastAPI dependency that returns the global EngineContainer.
    set_engines: Store the engine container at startup.
    clear_engines: Remove the engine container at shutdown.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Global engine instances (initialized at startup via app.py lifespan)
_engines: dict[str, Any] = {}


class EngineContainer:
    """Container for all engine instances shared across the application.

    Provides access to the database and engine instances that are initialized
    at startup and used throughout the application lifecycle. This avoids
    recreating expensive objects on each request.

    Attributes:
        db: Database connection manager.
        pricing: Pricing module reference for price lookups.
        thesis_engine: Thesis management and validation engine.
        signal_engine: Signal generation and scoring engine.
        risk_manager: Risk limits and exposure monitoring.
        principles_engine: Self-learning rules engine.
        broker: Broker implementation (mock or live).
    """

    def __init__(
        self,
        db: Any,
        pricing: Any,
        thesis_engine: Any,
        signal_engine: Any,
        risk_manager: Any,
        principles_engine: Any,
        broker: Any,
    ) -> None:
        """Initialize the engine container.

        Args:
            db: Database connection manager.
            pricing: Pricing module or service instance.
            thesis_engine: Thesis engine instance.
            signal_engine: Signal engine instance.
            risk_manager: Risk manager instance.
            principles_engine: Principles engine instance.
            broker: Broker implementation instance.
        """
        self.db = db
        self.pricing = pricing
        self.thesis_engine = thesis_engine
        self.signal_engine = signal_engine
        self.risk_manager = risk_manager
        self.principles_engine = principles_engine
        self.broker = broker


def get_engines() -> EngineContainer:
    """Dependency to provide engine instances to route handlers.

    Returns the global engine container that was initialized at application
    startup. Used as a FastAPI dependency to inject engines into route handlers.

    Returns:
        The shared EngineContainer with all initialized engine instances.

    Raises:
        RuntimeError: If called before the engines are initialized at startup.
    """
    if "container" not in _engines:
        raise RuntimeError("Engines not initialized. This should not happen.")
    return _engines["container"]


def set_engines(container: EngineContainer) -> None:
    """Store the engine container for dependency injection.

    Called by the application lifespan manager after initializing all engines.
    This must be called before any route handlers execute.

    Args:
        container: The initialized EngineContainer to make available.
    """
    _engines["container"] = container


def clear_engines() -> None:
    """Remove the engine container during shutdown.

    Called by the application lifespan manager during cleanup.
    """
    _engines.pop("container", None)
