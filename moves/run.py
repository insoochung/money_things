"""Entry point for running the Money Moves server in development/mock mode.

Sets up environment variables for testing mode and launches the FastAPI
application via uvicorn.

Usage:
    MOVES_TESTING=1 python3 run.py
    # or just: python3 run.py  (sets MOVES_TESTING=1 automatically)
"""

from __future__ import annotations

import os
import sys

# Mock mode (mock DB + mock broker) with real auth (Google OAuth).
# MOVES_MODE=mock controls data; MOVES_TESTING bypasses auth (don't want that).
os.environ.pop("MOVES_TESTING", None)
os.environ.setdefault("MOVES_MODE", "mock")

# Ensure session secret is set for auth middleware
if "MOVES_SESSION_SECRET_KEY" not in os.environ:
    os.environ["MOVES_SESSION_SECRET_KEY"] = "dev-secret-key-change-in-production"

# Ensure moves root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn

    from config.settings import setup_logging

    setup_logging()

    from api.app import create_app

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)
