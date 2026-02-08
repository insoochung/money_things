"""Enhanced approval workflow with auto-approve rules and signal modification.

Routes trading signals through configurable approval logic. Supports auto-approval
for low-value trades, high-confidence signals, and rebalance operations. Signals
that don't meet auto-approve criteria are routed to the user via Telegram.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from db.database import Database
from engine import ActorType, Signal, SignalStatus

logger = logging.getLogger(__name__)

# Default auto-approve thresholds (configurable via settings table)
DEFAULT_MAX_AUTO_VALUE = 500.0
DEFAULT_MIN_AUTO_CONFIDENCE = 0.9


def _audit(db: Database, action: str, detail: str) -> None:
    """Create an audit log entry."""
    now = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT INTO audit_log (actor, action, detail, timestamp) VALUES (?, ?, ?, ?)",
        (ActorType.ENGINE, action, detail, now),
    )


class ApprovalWorkflow:
    """Enhanced signal approval with auto-approve rules and modification support.

    Evaluates signals against configurable rules to determine if they can be
    auto-approved or need manual review. Supports modifying pending signals
    before approval.

    Attributes:
        db: Database instance for state management.
        signal_engine: Signal engine for creating/updating signals.
        broker: Broker instance for order preview.
        risk_manager: Risk manager for position limits.
    """

    def __init__(
        self,
        db: Database,
        signal_engine: Any,
        broker: Any,
        risk_manager: Any,
    ) -> None:
        """Initialize the approval workflow.

        Args:
            db: Database instance.
            signal_engine: Signal engine for signal management.
            broker: Broker for order preview and execution.
            risk_manager: Risk manager for checking limits.
        """
        self.db = db
        self.signal_engine = signal_engine
        self.broker = broker
        self.risk_manager = risk_manager

    def _get_setting(self, key: str, default: float) -> float:
        """Retrieve a numeric setting from the settings table.

        Args:
            key: Setting key name.
            default: Default value if key not found.

        Returns:
            The setting value as a float.
        """
        row = self.db.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row:
            try:
                return float(row["value"])
            except (ValueError, TypeError):
                pass
        return default

    def should_auto_approve(self, signal: Signal) -> bool:
        """Check if a signal meets auto-approve criteria.

        Auto-approve rules (any one sufficient):
        1. Estimated trade value < configurable threshold (default $500)
        2. Confidence > threshold AND thesis is confirmed
        3. Signal is a rebalance action (maintaining target weights)

        Args:
            signal: The signal to evaluate.

        Returns:
            True if the signal should be auto-approved.
        """
        # Rule 1: Low-value trades
        max_value = self._get_setting("auto_approve_max_value", DEFAULT_MAX_AUTO_VALUE)
        if signal.size_pct is not None:
            portfolio = self.db.fetch_one(
                "SELECT total_value FROM portfolio_value ORDER BY timestamp DESC LIMIT 1"
            )
            if portfolio:
                trade_value = portfolio["total_value"] * (signal.size_pct / 100)
                if trade_value < max_value:
                    logger.info(
                        "Auto-approve: %s %s value $%.0f < $%.0f threshold",
                        signal.action,
                        signal.symbol,
                        trade_value,
                        max_value,
                    )
                    return True

        # Rule 2: High confidence + confirmed thesis
        min_confidence = self._get_setting(
            "auto_approve_min_confidence", DEFAULT_MIN_AUTO_CONFIDENCE
        )
        if signal.confidence >= min_confidence and signal.thesis_id:
            thesis = self.db.fetch_one(
                "SELECT status FROM theses WHERE id = ?", (signal.thesis_id,)
            )
            if thesis and thesis["status"] == "confirmed":
                logger.info(
                    "Auto-approve: %s %s confidence=%.2f with confirmed thesis",
                    signal.action,
                    signal.symbol,
                    signal.confidence,
                )
                return True

        # Rule 3: Rebalance signals
        if str(signal.source) == "rebalance":
            logger.info("Auto-approve: rebalance signal for %s", signal.symbol)
            return True

        return False

    def process_signal(self, signal: Signal) -> dict[str, Any]:
        """Route a signal through the approval flow.

        If the signal meets auto-approve criteria, it is approved immediately.
        Otherwise, it remains pending for manual review.

        Args:
            signal: The signal to process.

        Returns:
            Dictionary with 'status' ('auto_approved' or 'pending') and 'signal_id'.
        """
        if self.should_auto_approve(signal):
            now = datetime.now(UTC).isoformat()
            self.db.execute(
                "UPDATE signals SET status = ?, decided_at = ? WHERE id = ?",
                (SignalStatus.APPROVED, now, signal.id),
            )
            _audit(
                self.db,
                "signal_auto_approved",
                f"Signal {signal.id}: {signal.action} {signal.symbol}",
            )
            return {"status": "auto_approved", "signal_id": signal.id}

        _audit(
            self.db,
            "signal_pending_approval",
            f"Signal {signal.id}: {signal.action} {signal.symbol} awaiting manual review",
        )
        return {"status": "pending", "signal_id": signal.id}

    def modify_signal(
        self,
        signal_id: int,
        size_override: float | None = None,
        price_override: float | None = None,
    ) -> dict[str, Any]:
        """Modify a pending signal's size or price before approval.

        Only signals in PENDING status can be modified.

        Args:
            signal_id: ID of the signal to modify.
            size_override: New size percentage (replaces size_pct).
            price_override: New limit price override.

        Returns:
            Dictionary with 'success' flag and 'message'.
        """
        row = self.db.fetch_one("SELECT id, status, symbol FROM signals WHERE id = ?", (signal_id,))
        if not row:
            return {"success": False, "message": f"Signal {signal_id} not found"}

        if row["status"] != SignalStatus.PENDING:
            return {
                "success": False,
                "message": f"Signal {signal_id} is {row['status']}, cannot modify",
            }

        updates: list[str] = []
        params: list[Any] = []

        if size_override is not None:
            updates.append("size_pct = ?")
            params.append(size_override)

        if price_override is not None:
            updates.append("funding_plan = ?")
            params.append(f'{{"limit_price": {price_override}}}')

        if not updates:
            return {"success": False, "message": "No modifications specified"}

        params.append(signal_id)
        self.db.execute(
            f"UPDATE signals SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

        detail = f"Signal {signal_id} modified:"
        if size_override is not None:
            detail += f" size_pct={size_override}"
        if price_override is not None:
            detail += f" limit_price={price_override}"
        _audit(self.db, "signal_modified", detail)

        return {"success": True, "message": detail}
