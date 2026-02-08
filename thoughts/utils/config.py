"""Centralized configuration loading for money_thoughts utilities.

This module is the single source of truth for all configuration values used
across the ``utils`` package. It handles three concerns:

1. **Environment variable loading** -- On import, it looks for a ``.env`` file
   in the project root (one directory above ``utils/``) and loads it via
   ``python-dotenv`` if available. This is a soft dependency: if ``dotenv`` is
   not installed, the module silently continues and relies on variables already
   present in the shell environment.

2. **API keys** -- Currently only ``FINNHUB_API_KEY`` is supported. It is
   optional; when absent, the Finnhub fallback in ``utils.price`` is simply
   skipped and yfinance remains the sole data source.

3. **Rate-limit constants** -- Each external API has its own rate-limit
   parameter that the corresponding module enforces via sleep-based throttling:

   - ``YFINANCE_DELAY`` (0.1 s between requests) -- conservative delay to
     avoid hitting Yahoo Finance's undocumented rate limits.
   - ``FINNHUB_RATE_LIMIT`` (60 req/min) -- matches the free-tier limit for
     Finnhub's REST API.
   - ``POLYMARKET_RATE_LIMIT`` (10 req/s) -- matches Polymarket's documented
     Gamma API limit.

4. **Polymarket API URLs** -- Base URLs for the Polymarket CLOB and Gamma
   APIs. These are unlikely to change but are centralised here so that a
   staging or mock endpoint can be swapped in during testing.

Importing this module has the side effect of loading ``.env`` (if present).
All other modules in the package import their settings from here rather than
reading ``os.getenv`` directly, ensuring a single point of change.
"""

from __future__ import annotations

import os
from pathlib import Path

# Load .env from project root if python-dotenv is available
_project_root = Path(__file__).parent.parent
_env_path = _project_root / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_path)
    except ImportError:
        pass

# API Keys (optional)
FINNHUB_API_KEY: str | None = os.getenv("FINNHUB_API_KEY")

# Rate limits
YFINANCE_DELAY: float = 0.1  # seconds between requests
FINNHUB_RATE_LIMIT: int = 60  # requests per minute
POLYMARKET_RATE_LIMIT: int = 10  # requests per second

# Polymarket API URLs
POLYMARKET_BASE_URL: str = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com"
