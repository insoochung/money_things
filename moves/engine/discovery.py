"""Universe scanning and stock discovery engine.

Discovers new tickers aligned with active theses by searching for companies
matching thesis universe_keywords. In mock mode, returns a static set of
well-known tickers for development.

Classes:
    DiscoveryEngine: Scans the investable universe for thesis-aligned tickers.
"""

from __future__ import annotations

import logging
from typing import Any

from db.database import Database

logger = logging.getLogger(__name__)

# Static sector mapping for known tickers (used in mock mode and sector exposure)
SECTOR_MAP: dict[str, str] = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOG": "Technology",
    "GOOGL": "Technology",
    "AMZN": "Consumer Cyclical",
    "NVDA": "Technology",
    "AMD": "Technology",
    "TSLA": "Consumer Cyclical",
    "META": "Technology",
    "AVGO": "Technology",
    "QCOM": "Technology",
    "INTC": "Technology",
    "CRM": "Technology",
    "ORCL": "Technology",
    "PANW": "Technology",
    "TEM": "Technology",
    "VST": "Utilities",
}


def get_sector(symbol: str) -> str:
    """Get the sector for a given ticker symbol.

    Args:
        symbol: Stock ticker symbol.

    Returns:
        Sector name string, or 'Unknown' if not in the mapping.
    """
    return SECTOR_MAP.get(symbol.upper(), "Unknown")


class DiscoveryEngine:
    """Scans the investable universe for thesis-aligned tickers.

    In mock mode, uses a static mapping of keywords to tickers.
    In production, would integrate with screening APIs (e.g., Finviz, yfinance screener).

    Attributes:
        db: Database instance for reading theses and writing discovered symbols.
    """

    def __init__(self, db: Database) -> None:
        """Initialize the discovery engine.

        Args:
            db: Database instance for thesis and signal access.
        """
        self.db = db

    def scan_universe(self) -> list[dict[str, Any]]:
        """Scan for new tickers aligned with active theses.

        Reads active theses with universe_keywords, searches for matching tickers,
        and returns any that are not already in the portfolio.

        Returns:
            List of dicts with 'symbol', 'thesis_id', and 'reason' keys for
            each newly discovered ticker.
        """
        theses = self.db.fetchall(
            "SELECT id, title, symbols, universe_keywords "
            "FROM theses WHERE status IN ('active', 'strengthening')"
        )

        discoveries: list[dict[str, Any]] = []
        existing_symbols = {
            row["symbol"]
            for row in self.db.fetchall("SELECT DISTINCT symbol FROM positions WHERE shares > 0")
        }

        for thesis in theses:
            keywords_raw = thesis.get("universe_keywords", "[]")
            try:
                import json

                keywords = (
                    json.loads(keywords_raw)
                    if isinstance(keywords_raw, str)
                    else keywords_raw
                )
            except (json.JSONDecodeError, TypeError):
                keywords = []

            for keyword in keywords:
                matched = self._search_keyword(keyword)
                for symbol in matched:
                    if symbol not in existing_symbols:
                        discoveries.append(
                            {
                                "symbol": symbol,
                                "thesis_id": thesis["id"],
                                "reason": (
                                    f"Matches keyword '{keyword}' "
                                    f"from thesis: {thesis['title']}"
                                ),
                            }
                        )
                        existing_symbols.add(symbol)

        logger.info("Universe scan found %d new tickers", len(discoveries))
        return discoveries

    def _search_keyword(self, keyword: str) -> list[str]:
        """Search for tickers matching a keyword.

        In mock mode, returns from a static mapping. Production would use
        a screening API.

        Args:
            keyword: Search keyword (e.g., 'AI', 'semiconductors').

        Returns:
            List of matching ticker symbols.
        """
        # Static keyword -> ticker mapping for mock mode
        keyword_map: dict[str, list[str]] = {
            "AI": ["NVDA", "AMD", "MSFT", "GOOG", "AVGO"],
            "semiconductors": ["NVDA", "AMD", "AVGO", "QCOM", "INTC"],
            "cloud": ["MSFT", "GOOG", "AMZN", "CRM", "ORCL"],
            "EV": ["TSLA"],
            "software": ["MSFT", "CRM", "ORCL", "PANW"],
            "hardware": ["AAPL", "NVDA", "AMD", "AVGO"],
        }
        kl = keyword.lower()
        for k, v in keyword_map.items():
            if kl == k.lower():
                return v
        return []
