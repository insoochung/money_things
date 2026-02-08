"""What-if engine for tracking hypothetical outcomes of rejected/ignored signals.

Records what would have happened if a signal had been acted on, enabling
learning about rejection accuracy and opportunity cost of ignored signals.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from db.database import Database
from engine.pricing import get_price

logger = logging.getLogger(__name__)


class WhatIfEngine:
    """Tracks hypothetical outcomes of rejected and ignored signals.

    When a signal is rejected or ignored, this engine records the price at
    that moment. It then periodically updates current prices to compute
    hypothetical P/L, answering: "What if we had taken that trade?"

    Attributes:
        db: Database instance for what_if table operations.
    """

    def __init__(self, db: Database) -> None:
        """Initialize the what-if engine.

        Args:
            db: Database instance with what_if and signals tables.
        """
        self.db = db

    def record_pass(self, signal_id: int, decision: str, price_at_pass: float) -> None:
        """Record a rejected or ignored signal for what-if tracking.

        Args:
            signal_id: ID of the signal that was passed on.
            decision: Either 'rejected' or 'ignored'.
            price_at_pass: Market price at the time of the decision.

        Raises:
            ValueError: If decision is not 'rejected' or 'ignored'.
        """
        if decision not in ("rejected", "ignored"):
            msg = f"Decision must be 'rejected' or 'ignored', got '{decision}'"
            raise ValueError(msg)

        self.db.execute(
            "INSERT INTO what_if (signal_id, decision, price_at_pass, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (signal_id, decision, price_at_pass, datetime.now(UTC).isoformat()),
        )
        self.db.connect().commit()
        logger.info(
            "Recorded what-if for signal %d (%s @ %.2f)",
            signal_id,
            decision,
            price_at_pass,
        )

    def update_all(self) -> int:
        """Refresh current prices and hypothetical P/L for all open what-ifs.

        Fetches current prices for all tracked symbols and updates the
        hypothetical_pnl and hypothetical_pnl_pct fields.

        Returns:
            Number of what-if records updated.
        """
        rows = self.db.fetchall(
            "SELECT w.id, w.signal_id, w.price_at_pass, s.symbol, s.action "
            "FROM what_if w JOIN signals s ON w.signal_id = s.id"
        )

        updated = 0
        now = datetime.now(UTC).isoformat()

        for row in rows:
            try:
                price_data = get_price(row["symbol"])
                current_price = price_data.get("price", 0.0)
                if not current_price:
                    continue

                pnl, pnl_pct = self._compute_hypothetical_pnl(
                    row["action"], row["price_at_pass"], current_price
                )

                self.db.execute(
                    "UPDATE what_if SET current_price = ?, hypothetical_pnl = ?, "
                    "hypothetical_pnl_pct = ?, updated_at = ? WHERE id = ?",
                    (current_price, pnl, pnl_pct, now, row["id"]),
                )
                updated += 1
            except Exception:
                logger.warning("Failed to update what-if %d", row["id"], exc_info=True)

        if updated:
            self.db.connect().commit()
        logger.info("Updated %d/%d what-if records", updated, len(rows))
        return updated

    def get_summary(self) -> dict[str, Any]:
        """Compute summary statistics for what-if tracking.

        Returns:
            Dict with pass_accuracy, reject_accuracy, ignore_cost,
            engagement_quality metrics.
        """
        rows = self.db.fetchall(
            "SELECT decision, hypothetical_pnl, hypothetical_pnl_pct FROM what_if "
            "WHERE hypothetical_pnl IS NOT NULL"
        )

        if not rows:
            return {
                "pass_accuracy": 0.0,
                "reject_accuracy": 0.0,
                "ignore_cost": 0.0,
                "engagement_quality": 0.0,
                "total_tracked": 0,
            }

        rejected = [r for r in rows if r["decision"] == "rejected"]
        ignored = [r for r in rows if r["decision"] == "ignored"]

        # Reject accuracy: % of rejections that would have lost money
        reject_accuracy = 0.0
        if rejected:
            correct_rejects = sum(1 for r in rejected if (r["hypothetical_pnl"] or 0) <= 0)
            reject_accuracy = correct_rejects / len(rejected)

        # Pass accuracy: % of all passes that were correct (would have lost)
        correct_passes = sum(1 for r in rows if (r["hypothetical_pnl"] or 0) <= 0)
        pass_accuracy = correct_passes / len(rows)

        # Ignore cost: avg hypothetical return of ignored signals (opportunity cost)
        ignore_cost = 0.0
        if ignored:
            ignore_cost = sum(r["hypothetical_pnl_pct"] or 0 for r in ignored) / len(ignored)

        # Engagement quality: reject_accuracy - (1 - pass_accuracy of ignored)
        ignore_accuracy = 0.0
        if ignored:
            correct_ignores = sum(1 for r in ignored if (r["hypothetical_pnl"] or 0) <= 0)
            ignore_accuracy = correct_ignores / len(ignored)
        engagement_quality = reject_accuracy - ignore_accuracy if rejected and ignored else 0.0

        return {
            "pass_accuracy": pass_accuracy,
            "reject_accuracy": reject_accuracy,
            "ignore_cost": ignore_cost,
            "engagement_quality": engagement_quality,
            "total_tracked": len(rows),
        }

    def list_whatifs(self, decision: str | None = None) -> list[dict[str, Any]]:
        """List what-if records, optionally filtered by decision type.

        Args:
            decision: Filter by 'rejected' or 'ignored'. None returns all.

        Returns:
            List of what-if records as dicts.
        """
        if decision:
            rows = self.db.fetchall(
                "SELECT w.*, s.symbol, s.action FROM what_if w "
                "JOIN signals s ON w.signal_id = s.id "
                "WHERE w.decision = ? ORDER BY w.id DESC",
                (decision,),
            )
        else:
            rows = self.db.fetchall(
                "SELECT w.*, s.symbol, s.action FROM what_if w "
                "JOIN signals s ON w.signal_id = s.id ORDER BY w.id DESC"
            )
        return rows

    @staticmethod
    def _compute_hypothetical_pnl(
        action: str, entry_price: float, current_price: float
    ) -> tuple[float, float]:
        """Compute hypothetical P/L for a what-if scenario.

        Args:
            action: Signal action (BUY, SELL, SHORT, COVER).
            entry_price: Price at time of signal rejection/ignore.
            current_price: Current market price.

        Returns:
            Tuple of (absolute_pnl, pnl_percentage).
        """
        if action in ("BUY", "COVER"):
            pnl = current_price - entry_price
        else:  # SELL, SHORT
            pnl = entry_price - current_price

        pnl_pct = pnl / entry_price if entry_price > 0 else 0.0
        return pnl, pnl_pct
