"""News-driven signal scanner: wraps NewsValidator for scheduled scanning.

Provides a simple interface for the job system to trigger news-based
thesis validation and signal generation.

Classes:
    NewsScanner: Scheduled news scanning for thesis validation.
"""

from __future__ import annotations

import logging
from typing import Any

from db.database import Database
from engine.news_validator import NewsValidator
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

logger = logging.getLogger(__name__)


class NewsScanner:
    """Wraps NewsValidator for scheduled scanning.

    The NewsValidator already does the heavy lifting: search news, score articles,
    auto-transition theses, and generate SELL signals on invalidation. This class
    provides a clean interface for the job scheduler.

    Attributes:
        validator: The underlying NewsValidator instance.
    """

    def __init__(
        self,
        db: Database,
        thesis_engine: ThesisEngine,
        signal_engine: SignalEngine,
    ) -> None:
        self.validator = NewsValidator(
            db=db,
            thesis_engine=thesis_engine,
            signal_engine=signal_engine,
        )

    def run_scan(self) -> list[dict[str, Any]]:
        """Run news validation for all active theses.

        Returns:
            List of validation result dicts from NewsValidator.validate_all().
        """
        logger.info("news_scan: starting scan")
        results = self.validator.validate_all()
        transitions = [r for r in results if r.get("transition")]
        logger.info(
            "news_scan: scanned %d theses, %d transitions triggered",
            len(results),
            len(transitions),
        )
        return results
