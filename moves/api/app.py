"""FastAPI application for the money_moves web dashboard and API.

This module creates and configures the main FastAPI application with:
- CORS middleware for cross-origin requests
- Google OAuth authentication middleware
- Lifespan hooks to initialize/cleanup engine instances
- Static file serving from dashboard/static/
- Jinja2 templates for the main dashboard HTML
- All API route modules mounted under /api/fund/
- WebSocket endpoint for real-time price streaming
- Health check endpoint

The application runs in two modes:
- Mock: Uses MockBroker and mock database for development/testing
- Live: Uses SchwabBroker and live database for real trading

Engine instances are created at startup and shared across all requests via
dependency injection. The database connection is also established at startup
and cleaned up at shutdown.

Functions:
    create_app: Factory function to create and configure the FastAPI application.
    get_engines: Dependency provider for engine instances.
    lifespan: Async context manager for application startup/shutdown.

Dependencies:
    Requires all engine modules, auth module, route modules, and WebSocket module.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from api.auth import AuthMiddleware, create_auth_router
from api.deps import EngineContainer, clear_engines, set_engines
from api.routes import admin, fund, intelligence, performance, risk, signals, theses, trades
from api.websocket import create_websocket_router
from broker.mock import MockBroker
from config.settings import Mode, get_settings
from db.database import Database
from engine import pricing as pricing_module
from engine.principles import PrinciplesEngine
from engine.risk import RiskManager
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

logger = logging.getLogger(__name__)


def _start_scheduler(db: Database, container: Any) -> Any:
    """Initialize and start the task scheduler with all default jobs.

    Creates a Scheduler instance, registers jobs with real engine callables,
    and starts the background scheduler. Returns the scheduler for shutdown.

    Args:
        db: Database instance.
        container: EngineContainer with engine references.

    Returns:
        The started Scheduler instance, or None if scheduler setup fails.
    """
    try:
        from functools import partial

        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        from engine.analytics import AnalyticsEngine
        from engine.congress import CongressTradesEngine
        from engine.jobs import (
            job_congress_trades,
            job_exposure_snapshot,
            job_nav_snapshot,
            job_price_update,
            job_signal_expiry,
            job_stale_thesis_check,
            job_whatif_update,
        )
        from engine.scheduler import Scheduler
        from engine.whatif import WhatIfEngine

        analytics = AnalyticsEngine(db=db)
        whatif = WhatIfEngine(db=db)
        congress = CongressTradesEngine(db=db, signal_engine=container.signal_engine)

        tz = "America/New_York"
        scheduler = Scheduler(db=db)

        # Price update: every 15 min, market hours
        scheduler.add_job(
            "price_update",
            partial(job_price_update, db),
            CronTrigger(minute="*/15", hour="9-15", day_of_week="mon-fri", timezone=tz),
        )

        # Signal expiry: every hour
        scheduler.add_job(
            "signal_expiry",
            partial(job_signal_expiry, container.signal_engine, db),
            IntervalTrigger(hours=1),
        )

        # NAV snapshot: 4:15 PM ET
        scheduler.add_job(
            "nav_snapshot",
            partial(job_nav_snapshot, analytics),
            CronTrigger(hour=16, minute=15, day_of_week="mon-fri", timezone=tz),
        )

        # What-if update: 4:30 PM ET
        scheduler.add_job(
            "whatif_update",
            partial(job_whatif_update, whatif),
            CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone=tz),
        )

        # Congress trades: 7:00 PM ET daily
        scheduler.add_job(
            "congress_trades",
            partial(job_congress_trades, congress),
            CronTrigger(hour=19, minute=0, timezone=tz),
        )

        # Exposure snapshot: hourly, market hours
        scheduler.add_job(
            "exposure_snapshot",
            partial(job_exposure_snapshot, analytics),
            CronTrigger(minute=0, hour="9-16", day_of_week="mon-fri", timezone=tz),
        )

        # Stale thesis check: Monday 8:00 AM ET
        scheduler.add_job(
            "stale_thesis_check",
            partial(job_stale_thesis_check, container.thesis_engine, db),
            CronTrigger(day_of_week="mon", hour=8, minute=0, timezone=tz),
        )

        scheduler.start()
        logger.info("Scheduler started with %d jobs", len(scheduler._jobs))
        return scheduler

    except Exception:
        logger.exception("Failed to start scheduler — continuing without it")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Application lifespan manager for startup and shutdown tasks.

    Handles initialization and cleanup of shared resources:

    Startup:
    - Initialize database connection and schema
    - Create engine instances (pricing, thesis, signal, risk, principles)
    - Create broker instance (mock mode only in Phase 2.1)
    - Store in global container for dependency injection

    Shutdown:
    - Close database connections
    - Clean up any background tasks

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the application after startup is complete.
    """
    # Startup
    settings = get_settings()
    logger.info("Starting money_moves in %s mode", settings.mode.upper())

    try:
        # Initialize database
        db = Database(settings.get_db_path())
        db.init_schema()
        logger.info("Database initialized at %s", settings.get_db_path())

        # Initialize engines
        thesis_engine = ThesisEngine(db=db)
        signal_engine = SignalEngine(db=db)
        risk_manager = RiskManager(db=db)
        principles_engine = PrinciplesEngine(db=db)

        # Initialize broker (mock mode only for Phase 2.1)
        if settings.mode == Mode.MOCK:
            broker = MockBroker(db=db)
        else:
            # TODO: Initialize SchwabBroker in Phase 4
            raise NotImplementedError("Live mode not yet implemented")

        # Store in global container for dependency injection
        container = EngineContainer(
            db=db,
            pricing=pricing_module,
            thesis_engine=thesis_engine,
            signal_engine=signal_engine,
            risk_manager=risk_manager,
            principles_engine=principles_engine,
            broker=broker,
        )
        set_engines(container)

        logger.info("All engines initialized successfully")

        # Start scheduler (skip in testing mode)
        scheduler = None
        if not settings.testing:
            scheduler = _start_scheduler(db, container)

        yield

    except Exception as e:
        logger.error("Failed to initialize application: %s", e)
        raise
    finally:
        # Shutdown scheduler
        if scheduler is not None:
            try:
                scheduler.stop()
                logger.info("Scheduler stopped")
            except Exception:
                logger.exception("Error stopping scheduler")

        # Shutdown
        try:
            from api.deps import get_engines as _get

            c = _get()
            c.db.close()
            logger.info("Database connection closed")
        except RuntimeError:
            pass
        clear_engines()
        logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Sets up the complete web application with all middleware, routes, and
    static file serving. This is the main entry point for the application.

    Returns:
        Configured FastAPI application ready to serve requests.
    """
    app = FastAPI(
        title="Money Moves",
        description="Autonomous investment execution engine",
        version="2.1.0",
        lifespan=lifespan,
    )

    # CORS middleware for dashboard
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],  # Dashboard dev server
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    # Authentication middleware (protects all routes except /auth/* and /health)
    app.add_middleware(AuthMiddleware)

    # Health check endpoint (unprotected)
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint for monitoring and load balancers.

        Returns basic application status information. This endpoint is not
        protected by authentication middleware.

        Returns:
            Dictionary with status and mode information.
        """
        settings = get_settings()
        return {"status": "healthy", "mode": settings.mode, "version": "2.1.0"}

    # Authentication routes
    app.include_router(create_auth_router())

    # API routes
    app.include_router(fund.router, prefix="/api/fund", tags=["fund"])
    app.include_router(theses.router, prefix="/api/fund", tags=["theses"])
    app.include_router(signals.router, prefix="/api/fund", tags=["signals"])
    app.include_router(trades.router, prefix="/api/fund", tags=["trades"])
    app.include_router(performance.router, prefix="/api/fund", tags=["performance"])
    app.include_router(risk.router, prefix="/api/fund", tags=["risk"])
    app.include_router(intelligence.router, prefix="/api/fund", tags=["intelligence"])
    app.include_router(admin.router, prefix="/api/fund", tags=["admin"])

    # WebSocket for real-time prices
    app.include_router(create_websocket_router())

    # Dashboard static files (CSS, JS served from /dashboard/)
    dashboard_dir = Path(__file__).parent.parent / "dashboard"

    if dashboard_dir.exists() and (dashboard_dir / "index.html").exists():
        app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")

        @app.get("/", response_class=HTMLResponse)
        async def dashboard() -> HTMLResponse:
            """Redirect root to the dashboard index page.

            Serves the dashboard index.html which loads CSS/JS from /dashboard/.

            Returns:
                HTML response with the dashboard page.
            """
            return HTMLResponse((dashboard_dir / "index.html").read_text())

    else:
        # Placeholder response when dashboard files don't exist
        @app.get("/", response_class=HTMLResponse)
        async def dashboard_placeholder() -> HTMLResponse:
            """Placeholder dashboard when static files are not available.

            Returns a simple HTML page indicating the dashboard is under construction.
            This is used during Phase 2.1 before the dashboard files are created.

            Returns:
                Simple HTML page with construction message.
            """
            return HTMLResponse("""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Money Moves Dashboard</title>
                <style>
                    body { font-family: Inter, sans-serif; margin: 2rem; }
                    .status { color: #37352f; font-size: 1.1rem; }
                </style>
            </head>
            <body>
                <h1>Money Moves Dashboard</h1>
                <p class="status">FastAPI backend is running ✅</p>
                <p>Dashboard UI will be available in Phase 2.2</p>
                <p><a href="/docs">View API documentation</a></p>
            </body>
            </html>
            """)

    return app


# Development server entry point
if __name__ == "__main__":
    from config.settings import setup_logging

    setup_logging()
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
