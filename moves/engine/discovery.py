"""Universe scanning and stock discovery engine.

All methods now accept user_id for multi-user scoping.
"""

from __future__ import annotations

import logging
from typing import Any

from db.database import Database

logger = logging.getLogger(__name__)

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
    """Get the sector for a given ticker symbol."""
    return SECTOR_MAP.get(symbol.upper(), "Unknown")


class DiscoveryEngine:
    """Scans the investable universe for thesis-aligned tickers."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def scan_universe(self, user_id: int) -> list[dict[str, Any]]:
        """Scan for new tickers aligned with a user's active theses.

        Args:
            user_id: ID of the owning user.

        Returns:
            List of dicts with 'symbol', 'thesis_id', and 'reason' keys.
        """
        theses = self.db.fetchall(
            "SELECT id, title, symbols, universe_keywords "
            "FROM theses WHERE status IN ('active', 'strengthening') AND user_id = ?",
            (user_id,),
        )

        discoveries: list[dict[str, Any]] = []
        existing_symbols = {
            row["symbol"]
            for row in self.db.fetchall(
                "SELECT DISTINCT symbol FROM positions WHERE shares > 0 AND user_id = ?",
                (user_id,),
            )
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
        """Search for tickers matching a keyword."""
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
