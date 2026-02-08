"""API route modules for the money_moves web application.

This package contains all the REST API endpoint implementations organized
by functional area. Each module defines a FastAPI router that is included
in the main application.

Modules:
    fund: Basic portfolio information (status, positions, exposure)
    theses: Investment thesis management
    signals: Trading signal approval and management
    trades: Trade execution history
    performance: Performance metrics and benchmarking
    risk: Risk analysis and monitoring
    intelligence: Unique features (Congress trades, principles, what-if)
    admin: Administrative functions (kill switch, mode switch, audit)

All routes require authentication via the auth middleware except for
health checks. The routes use dependency injection to access the
shared engine container with database and service instances.
"""

# Import all route modules for easy access
from . import admin, fund, intelligence, performance, risk, signals, theses, trades, users

__all__ = [
    "fund",
    "theses",
    "signals",
    "trades",
    "performance",
    "risk",
    "intelligence",
    "admin",
    "users",
]
