"""Environment-based configuration with mode switching (mock/live).

This module provides centralized configuration for the money_moves system using
pydantic-settings for environment variable binding and validation. All configuration
values can be set via environment variables with the MOVES_ prefix, or via a .env
file at config/.env.

The configuration supports two execution modes:
    - MOCK: Uses a simulated broker with fake fills at yfinance prices. Database
      stored at data/moves_mock.db. For development, testing, and paper trading.
    - LIVE: Uses the real Schwab API for live trading with real money. Database
      stored at data/moves_live.db. Requires Schwab API credentials.

Configuration categories:
    - Mode: Mock vs live execution
    - Database: Path to SQLite database file
    - Schwab: API credentials for live trading (app key, secret, account hash)
    - Telegram: Bot token and chat ID for signal approval notifications
    - Pricing: yfinance request delay, Finnhub API key for fallback
    - Risk Limits: Default values for position size, exposure, drawdown limits
    - Domain Expertise: Configurable expertise domains and scoring multipliers

Environment variables:
    All settings are bound to environment variables with the MOVES_ prefix:
        MOVES_MODE=mock (or live)
        MOVES_DB_PATH=/custom/path/to/db
        MOVES_SCHWAB_APP_KEY=xxx
        MOVES_TELEGRAM_TOKEN=xxx
        etc.

Classes:
    Mode: Enum for execution mode (mock/live).
    Settings: Pydantic settings model with all configuration values.

Functions:
    get_settings: Create and return a Settings instance.
    setup_logging: Configure Python logging for the application.

Module-level constants:
    PROJECT_ROOT: Absolute path to the moves/ directory.
    DATA_DIR: Absolute path to the moves/data/ directory.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


class Mode(StrEnum):
    """Execution mode for the money_moves system.

    Controls which broker implementation is used and which database file is selected.

    Values:
        MOCK: Use MockBroker with simulated fills. Database: data/moves_mock.db.
        LIVE: Use SchwabBroker with real execution. Database: data/moves_live.db.
    """

    MOCK = "mock"
    LIVE = "live"


class Settings(BaseSettings):
    """Centralized configuration for the money_moves system.

    All settings can be overridden via environment variables with the MOVES_ prefix,
    or via a .env file at config/.env. Pydantic-settings handles parsing, validation,
    and type coercion automatically.

    Attributes:
        mode: Execution mode (mock or live). Controls broker selection and DB path.
        db_path: Override path for the SQLite database. If empty, auto-selected
            based on mode (data/moves_mock.db or data/moves_live.db).
        schwab_app_key: Schwab API application key (required for live mode).
        schwab_secret: Schwab API secret (required for live mode).
        schwab_account_hash: Schwab account hash for identifying the trading account.
        telegram_token: Telegram bot token for signal approval notifications.
        telegram_chat_id: Telegram chat ID to send notifications to.
        yfinance_delay: Minimum delay in seconds between yfinance API requests.
        finnhub_api_key: Finnhub API key for fallback price data (not yet implemented).
        max_position_pct: Maximum single position as percentage of NAV (default 15%).
        max_sector_pct: Maximum sector concentration (default 35%).
        max_gross_exposure: Maximum gross exposure as ratio of NAV (default 1.50 = 150%).
        net_exposure_min: Minimum net exposure as ratio of NAV (default -0.30 = -30%).
        net_exposure_max: Maximum net exposure as ratio of NAV (default 1.30 = 130%).
        max_drawdown: Maximum drawdown from peak before trading halt (default 20%).
        daily_loss_limit: Maximum daily loss before trading halt (default 3%).
        expertise_domains: List of domains where the user has expertise. Signals in
            these domains receive a confidence boost during scoring.
        domain_boost: Multiplier for in-domain signals (default 1.15 = 15% boost).
        out_of_domain_penalty: Multiplier for out-of-domain signals (default 0.90 = 10% penalty).
    """

    model_config = {"env_prefix": "MOVES_", "env_file": str(PROJECT_ROOT / "config" / ".env")}

    mode: Mode = Mode.MOCK

    # Database
    db_path: str = ""

    # Schwab
    schwab_app_key: str = ""
    schwab_secret: str = ""
    schwab_account_hash: str = ""

    # Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Pricing
    yfinance_delay: float = 1.0
    finnhub_api_key: str = ""

    # Risk Limits
    max_position_pct: float = 0.15
    max_sector_pct: float = 0.35
    max_gross_exposure: float = 1.50
    net_exposure_min: float = -0.30
    net_exposure_max: float = 1.30
    max_drawdown: float = 0.20
    daily_loss_limit: float = 0.03

    # Domain Expertise
    expertise_domains: list[str] = Field(
        default_factory=lambda: ["AI", "semiconductors", "software", "hardware"]
    )
    domain_boost: float = 1.15
    out_of_domain_penalty: float = 0.90

    def get_db_path(self) -> Path:
        """Determine the database file path based on configuration.

        If db_path is explicitly set (via env var or .env file), uses that path.
        Otherwise, selects the default path based on the execution mode:
            - Mock mode: data/moves_mock.db
            - Live mode: data/moves_live.db

        Returns:
            Path object pointing to the SQLite database file.
        """
        if self.db_path:
            return Path(self.db_path)
        if self.mode == Mode.MOCK:
            return DATA_DIR / "moves_mock.db"
        return DATA_DIR / "moves_live.db"


def get_settings() -> Settings:
    """Create and return a Settings instance.

    Reads configuration from environment variables (MOVES_* prefix) and the
    .env file at config/.env. Each call creates a new Settings instance.

    Returns:
        Configured Settings instance with all values resolved.
    """
    return Settings()


def setup_logging() -> None:
    """Configure Python logging for the money_moves application.

    Sets up basic logging with INFO level, timestamp format, and a message format
    that includes the logger name and level. This should be called once at
    application startup.

    Side effects:
        - Configures the root logger with basicConfig.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
