"""Central orchestrator for the Money Moves system.

Coordinates startup, shutdown, and the main signal processing pipeline. All engine
modules (thesis, signals, risk, principles, approval, what-if, discovery) are
initialized here and composed into a unified workflow.

Classes:
    MoneyMovesCore: System orchestrator that wires together all engines.
"""

from __future__ import annotations

import logging
from typing import Any

from broker.base import Broker
from db.database import Database
from engine import (
    ActorType,
    Order,
    OrderType,
    Signal,
    SignalStatus,
)
from engine.approval import ApprovalWorkflow
from engine.discovery import DiscoveryEngine
from engine.principles import PrinciplesEngine
from engine.risk import RiskManager
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine
from engine.whatif import WhatIfEngine

logger = logging.getLogger(__name__)


class MoneyMovesCore:
    """Central orchestrator for the Money Moves system.

    Wires together all engine modules and provides high-level methods for
    startup, shutdown, signal processing, and system health monitoring.

    Attributes:
        db: Database instance.
        broker: Broker implementation (mock or live).
        settings: Configuration dict with mode, auto-approve thresholds, etc.
        thesis_engine: Manages investment theses.
        signal_engine: Signal creation, scoring, and lifecycle.
        risk_manager: Pre-trade risk checks and kill switch.
        principles_engine: Self-learning investment rules.
        whatif_engine: Tracks hypothetical outcomes of passed signals.
        discovery_engine: Scans for new thesis-aligned tickers.
        approval_workflow: Auto-approve logic and routing.
    """

    def __init__(
        self, db: Database, broker: Broker, settings: dict[str, Any] | None = None
    ) -> None:
        """Initialize all engines.

        Args:
            db: Database instance (schema must already be initialized).
            broker: Broker implementation for order execution.
            settings: Optional configuration dict. Keys used:
                - mode: 'mock' or 'live'
        """
        self.db = db
        self.broker = broker
        self.settings = settings or {}

        self.thesis_engine = ThesisEngine(db)
        self.signal_engine = SignalEngine(db)
        self.risk_manager = RiskManager(db)
        self.principles_engine = PrinciplesEngine(db)
        self.whatif_engine = WhatIfEngine(db)
        self.discovery_engine = DiscoveryEngine(db)
        self.approval_workflow = ApprovalWorkflow(
            db=db,
            signal_engine=self.signal_engine,
            broker=broker,
            risk_manager=self.risk_manager,
        )

    async def startup(self) -> dict[str, Any]:
        """Initialize all engines and verify system health.

        Checks database connectivity, broker connection, risk limits, and kill
        switch status. Logs a startup summary.

        Returns:
            Dict with startup status and any warnings.
        """
        warnings: list[str] = []

        # Check DB connectivity
        try:
            self.db.fetchone("SELECT 1")
        except Exception as exc:
            msg = f"Database connectivity check failed: {exc}"
            logger.error(msg)
            return {"status": "error", "message": msg}

        # Check broker connection
        try:
            await self.broker.get_account_balance()
        except Exception as exc:
            warnings.append(f"Broker connection issue: {exc}")
            logger.warning("Broker connectivity check failed: %s", exc)

        # Check risk limits exist
        limits = self.db.fetchall("SELECT * FROM risk_limits")
        if not limits:
            warnings.append("No risk limits configured")

        # Check kill switch
        kill_active = self.risk_manager.is_kill_switch_active()
        if kill_active:
            warnings.append("Kill switch is ACTIVE — trading halted")

        # Count pending signals
        pending = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM signals WHERE status = ?",
            (SignalStatus.PENDING,),
        )
        pending_count = pending["cnt"] if pending else 0

        mode = self.settings.get("mode", "mock")
        logger.info(
            "MoneyMovesCore started: mode=%s, kill_switch=%s, pending_signals=%d, warnings=%d",
            mode,
            kill_active,
            pending_count,
            len(warnings),
        )

        _audit(self.db, "system_startup", f"Mode: {mode}, warnings: {len(warnings)}")

        return {
            "status": "ok" if not warnings else "ok_with_warnings",
            "mode": mode,
            "kill_switch_active": kill_active,
            "pending_signals": pending_count,
            "risk_limits_count": len(limits),
            "warnings": warnings,
        }

    async def shutdown(self) -> None:
        """Gracefully shut down all engines.

        Logs shutdown and closes database connection.
        """
        logger.info("MoneyMovesCore shutting down")
        _audit(self.db, "system_shutdown", "Graceful shutdown")
        self.db.close()

    async def process_signal(self, signal_id: int) -> dict[str, Any]:
        """Full signal processing pipeline: risk check → approval → execution.

        Loads the signal, runs pre-trade risk checks, and either auto-approves
        and executes or leaves it pending for manual approval.

        Args:
            signal_id: ID of the signal to process.

        Returns:
            Dict with processing result including status and any error message.
        """
        signal = self.signal_engine.get_signal(signal_id)
        if not signal:
            return {"status": "error", "message": f"Signal {signal_id} not found"}

        if signal.status != SignalStatus.PENDING:
            return {
                "status": "error",
                "message": f"Signal {signal_id} is {signal.status}, expected pending",
            }

        # Pre-trade risk checks
        risk_result = self.risk_manager.pre_trade_check(signal)
        if not risk_result:
            self.signal_engine.cancel_signal(signal_id)
            return {
                "status": "risk_blocked",
                "signal_id": signal_id,
                "reason": risk_result.reason,
            }

        # Route through approval workflow
        approval_result = self.approval_workflow.process_signal(signal)

        if approval_result["status"] == "auto_approved":
            exec_result = await self.execute_approved_signal(signal_id)
            return {
                "status": "executed" if exec_result.get("status") == "executed" else "exec_failed",
                "signal_id": signal_id,
                "execution": exec_result,
            }

        # Signal remains pending for manual Telegram approval
        return {
            "status": "pending_approval",
            "signal_id": signal_id,
        }

    async def execute_approved_signal(self, signal_id: int) -> dict[str, Any]:
        """Execute an approved signal through the broker.

        Loads the signal, creates an order, executes via the broker, records
        the trade, and updates positions/lots.

        Args:
            signal_id: ID of the approved signal.

        Returns:
            Dict with execution result including trade details or error.
        """
        signal = self.signal_engine.get_signal(signal_id)
        if not signal:
            return {"status": "error", "message": f"Signal {signal_id} not found"}

        # Build order
        shares = self._estimate_shares(signal)
        order = Order(
            signal_id=signal_id,
            symbol=signal.symbol,
            action=signal.action,
            order_type=OrderType.MARKET,
            shares=shares,
        )

        try:
            result = await self.broker.place_order(order)
        except Exception as exc:
            logger.error("Order execution failed for signal %d: %s", signal_id, exc)
            _audit(self.db, "order_failed", f"Signal {signal_id}: {exc}")
            return {"status": "error", "message": str(exc)}

        if result.filled_price is not None:
            self.signal_engine.mark_executed(signal_id)
            _audit(
                self.db,
                "signal_executed",
                f"Signal {signal_id}: {signal.action} {shares} {signal.symbol} "
                f"@ {result.filled_price:.2f}",
            )

        return {
            "status": "executed" if result.filled_price else "not_filled",
            "signal_id": signal_id,
            "order_id": result.order_id,
            "filled_price": result.filled_price,
            "filled_shares": result.filled_shares,
            "message": result.message,
        }

    def get_system_status(self) -> dict[str, Any]:
        """Return full system health status.

        Returns:
            Dict with DB status, broker status, kill switch state,
            pending signals count, portfolio value, and exposure.
        """
        # DB status
        try:
            self.db.fetchone("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False

        # Kill switch
        kill_active = self.risk_manager.is_kill_switch_active()

        # Pending signals
        pending = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM signals WHERE status = ?",
            (SignalStatus.PENDING,),
        )
        pending_count = pending["cnt"] if pending else 0

        # Portfolio value
        pv = self.db.fetchone("SELECT * FROM portfolio_value ORDER BY date DESC LIMIT 1")

        # Exposure
        exposure = self.risk_manager.calculate_exposure()

        return {
            "db_connected": db_ok,
            "kill_switch_active": kill_active,
            "pending_signals": pending_count,
            "portfolio_value": pv["total_value"] if pv else 0,
            "cash": pv["cash"] if pv else 0,
            "exposure": exposure,
            "mode": self.settings.get("mode", "mock"),
        }

    def _estimate_shares(self, signal: Signal) -> float:
        """Estimate number of shares for an order based on signal size_pct.

        Args:
            signal: Signal with optional size_pct.

        Returns:
            Estimated share count (minimum 1).
        """
        if not signal.size_pct:
            return 1.0

        nav = self.risk_manager._get_nav()
        if nav <= 0:
            return 1.0

        from engine.pricing import get_price

        try:
            price_data = get_price(signal.symbol)
            price = price_data.get("price", 0)
            if price > 0:
                target_value = nav * signal.size_pct
                return max(1.0, round(target_value / price))
        except Exception:
            logger.warning("Could not estimate shares for %s", signal.symbol)

        return 1.0


def _audit(db: Database, action: str, details: str = "") -> None:
    """Create an audit log entry for core orchestrator actions."""
    db.execute(
        "INSERT INTO audit_log (actor, action, details, entity_type, entity_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (ActorType.ENGINE.value, action, details, "system", None),
    )
    db.connect().commit()
