"""Risk management engine: pre-trade checks, kill switch, exposure, and drawdown.

All methods now accept user_id for multi-user scoping.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from db.database import Database
from engine import ActorType, Signal, SignalAction

logger = logging.getLogger(__name__)


class RiskCheckResult:
    """Result of a single risk check."""

    def __init__(self, passed: bool, reason: str = "") -> None:
        self.passed = passed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.passed


class RiskManager:
    """Portfolio risk management engine."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def pre_trade_check(self, signal: Signal, user_id: int) -> RiskCheckResult:
        """Run all pre-trade risk checks against a signal.

        Args:
            signal: The Signal object to validate.
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult with passed/failed status.
        """
        checks = [
            self.check_kill_switch(user_id),
            self.check_position_size(signal, user_id),
            self.check_sector_concentration(signal, user_id),
            self.check_gross_exposure(user_id),
            self.check_net_exposure(user_id),
            self.check_trading_window(signal, user_id),
            self.check_drawdown_limit(user_id),
            self.check_daily_loss_limit(user_id),
        ]
        for check in checks:
            if not check.passed:
                _audit(self.db, "risk_check_failed", "signal", signal.id, check.reason)
                return check
        return RiskCheckResult(True)

    def check_kill_switch(self, user_id: int) -> RiskCheckResult:
        """Check if the user's kill switch is active.

        Args:
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        row = self.db.fetchone(
            "SELECT active FROM kill_switch WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        if row and row["active"]:
            return RiskCheckResult(False, "Kill switch is active - trading halted")
        return RiskCheckResult(True)

    def check_position_size(self, signal: Signal, user_id: int) -> RiskCheckResult:
        """Check if the signal would exceed max single-position allocation.

        Args:
            signal: The Signal to validate.
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        limit = self._get_limit("max_position_pct", 0.15, user_id)
        nav = self._get_nav(user_id)
        if nav <= 0:
            return RiskCheckResult(True)

        pos = self.db.fetchone(
            "SELECT shares, avg_cost FROM positions WHERE symbol = ? AND user_id = ?",
            (signal.symbol, user_id),
        )
        current_value = (pos["shares"] * pos["avg_cost"]) if pos else 0

        if signal.size_pct:
            new_value = current_value + (signal.size_pct * nav)
        else:
            new_value = current_value

        if new_value / nav > limit:
            return RiskCheckResult(
                False,
                f"Position size {new_value / nav:.1%} exceeds limit"
                f" {limit:.0%} for {signal.symbol}",
            )
        return RiskCheckResult(True)

    def check_sector_concentration(self, signal: Signal, user_id: int) -> RiskCheckResult:
        """Check sector concentration. Currently pass-through.

        Args:
            signal: The Signal to validate.
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult (always passes).
        """
        return RiskCheckResult(True)

    def check_gross_exposure(self, user_id: int) -> RiskCheckResult:
        """Check if total gross exposure exceeds the portfolio limit.

        Args:
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        limit = self._get_limit("max_gross_exposure", 1.50, user_id)
        exposure = self.calculate_exposure(user_id)
        if exposure["gross_pct"] > limit:
            return RiskCheckResult(
                False,
                f"Gross exposure {exposure['gross_pct']:.1%} exceeds limit {limit:.0%}",
            )
        return RiskCheckResult(True)

    def check_net_exposure(self, user_id: int) -> RiskCheckResult:
        """Check if net exposure falls within the allowed band.

        Args:
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        net_min = self._get_limit("net_exposure_min", -0.30, user_id)
        net_max = self._get_limit("net_exposure_max", 1.30, user_id)
        exposure = self.calculate_exposure(user_id)
        net = exposure["net_pct"]
        if net < net_min or net > net_max:
            return RiskCheckResult(
                False,
                f"Net exposure {net:.1%} outside band [{net_min:.0%}, {net_max:.0%}]",
            )
        return RiskCheckResult(True)

    def check_trading_window(self, signal: Signal, user_id: int) -> RiskCheckResult:
        """Check META-specific trading window restrictions.

        Args:
            signal: The Signal to validate.
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        if signal.symbol != "META":
            return RiskCheckResult(True)

        now = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.db.fetchone(
            """SELECT * FROM trading_windows
               WHERE symbol = 'META' AND opens <= ? AND closes >= ? AND user_id = ?""",
            (now, now, user_id),
        )
        if not row:
            if signal.action == SignalAction.SELL:
                return RiskCheckResult(True)
            return RiskCheckResult(False, "META trading window is closed")
        return RiskCheckResult(True)

    def check_drawdown_limit(self, user_id: int) -> RiskCheckResult:
        """Check if portfolio drawdown from peak NAV exceeds the maximum.

        Args:
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        limit = self._get_limit("max_drawdown", 0.20, user_id)
        dd = self.current_drawdown(user_id)
        if dd > limit:
            return RiskCheckResult(
                False,
                f"Drawdown {dd:.1%} exceeds limit {limit:.0%} - trading halted",
            )
        return RiskCheckResult(True)

    def check_daily_loss_limit(self, user_id: int) -> RiskCheckResult:
        """Check if today's portfolio loss exceeds the daily loss limit.

        Args:
            user_id: ID of the owning user.

        Returns:
            RiskCheckResult.
        """
        limit = self._get_limit("daily_loss_limit", 0.03, user_id)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        pv = self.db.fetchone(
            "SELECT daily_return_pct FROM portfolio_value WHERE date = ? AND user_id = ?",
            (today, user_id),
        )
        if pv and pv["daily_return_pct"] is not None:
            if pv["daily_return_pct"] < -limit:
                return RiskCheckResult(
                    False,
                    f"Daily loss {pv['daily_return_pct']:.1%} exceeds limit {limit:.0%}",
                )
        return RiskCheckResult(True)

    # --- Kill Switch ---

    def activate_kill_switch(self, reason: str, activated_by: str = "user", user_id: int = 0) -> None:
        """Activate the user's kill switch.

        Args:
            reason: Reason for activation.
            activated_by: Who activated the kill switch.
            user_id: ID of the owning user.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """INSERT INTO kill_switch (active, activated_at, reason, activated_by, user_id)
               VALUES (TRUE, ?, ?, ?, ?)""",
            (now, reason, activated_by, user_id),
        )
        self.db.connect().commit()
        _audit(self.db, "kill_switch_activated", "kill_switch", None, reason)

    def deactivate_kill_switch(self, user_id: int) -> None:
        """Deactivate the user's kill switch.

        Args:
            user_id: ID of the owning user.
        """
        now = datetime.now(UTC).isoformat()
        row = self.db.fetchone(
            "SELECT id FROM kill_switch WHERE active = TRUE AND user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        if row:
            self.db.execute(
                "UPDATE kill_switch SET active = FALSE, deactivated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            self.db.connect().commit()
        _audit(self.db, "kill_switch_deactivated", "kill_switch", None)

    def is_kill_switch_active(self, user_id: int) -> bool:
        """Check whether the user's kill switch is currently active.

        Args:
            user_id: ID of the owning user.

        Returns:
            True if there is an active kill_switch record.
        """
        row = self.db.fetchone(
            "SELECT active FROM kill_switch WHERE active = TRUE AND user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return bool(row)

    # --- Exposure ---

    def calculate_exposure(self, user_id: int) -> dict:
        """Calculate current portfolio exposure metrics.

        Args:
            user_id: ID of the owning user.

        Returns:
            Dictionary with exposure metrics.
        """
        nav = self._get_nav(user_id)
        if nav <= 0:
            return {
                "long_value": 0,
                "short_value": 0,
                "gross_value": 0,
                "net_value": 0,
                "gross_pct": 0,
                "net_pct": 0,
                "long_pct": 0,
                "short_pct": 0,
            }

        positions = self.db.fetchall(
            "SELECT * FROM positions WHERE shares > 0 AND user_id = ?",
            (user_id,),
        )
        long_value = 0.0
        short_value = 0.0

        for pos in positions:
            value = pos["shares"] * pos["avg_cost"]
            if pos.get("side", "long") == "long":
                long_value += value
            else:
                short_value += value

        gross = long_value + short_value
        net = long_value - short_value

        return {
            "long_value": long_value,
            "short_value": short_value,
            "gross_value": gross,
            "net_value": net,
            "gross_pct": gross / nav if nav else 0,
            "net_pct": net / nav if nav else 0,
            "long_pct": long_value / nav if nav else 0,
            "short_pct": short_value / nav if nav else 0,
        }

    # --- Drawdown ---

    def current_drawdown(self, user_id: int) -> float:
        """Calculate the current drawdown from peak portfolio NAV.

        Args:
            user_id: ID of the owning user.

        Returns:
            Float between 0.0 and 1.0 representing the drawdown percentage.
        """
        rows = self.db.fetchall(
            "SELECT total_value FROM portfolio_value WHERE user_id = ? ORDER BY date",
            (user_id,),
        )
        if not rows:
            return 0.0

        peak = 0.0
        current = 0.0
        for r in rows:
            val = r["total_value"]
            if val > peak:
                peak = val
            current = val

        if peak <= 0:
            return 0.0
        return (peak - current) / peak

    # --- Helpers ---

    def _get_nav(self, user_id: int) -> float:
        """Get the most recent portfolio NAV for a user.

        Args:
            user_id: ID of the owning user.

        Returns:
            The most recent total_value, or 0.0 if none exist.
        """
        pv = self.db.fetchone(
            "SELECT total_value FROM portfolio_value WHERE user_id = ? ORDER BY date DESC LIMIT 1",
            (user_id,),
        )
        return pv["total_value"] if pv else 0.0

    def _get_limit(self, limit_type: str, default: float, user_id: int) -> float:
        """Get a risk limit value for a user.

        Args:
            limit_type: The limit type key.
            default: The default value.
            user_id: ID of the owning user.

        Returns:
            The configured limit value, or the default.
        """
        row = self.db.fetchone(
            "SELECT value, enabled FROM risk_limits WHERE limit_type = ? AND user_id = ?",
            (limit_type, user_id),
        )
        if row and row["enabled"]:
            return row["value"]
        return default


def _audit(
    db: Database, action: str, entity_type: str, entity_id: int | None, details: str = ""
) -> None:
    """Create an audit log entry for a risk management action."""
    db.execute(
        """INSERT INTO audit_log (actor, action, details, entity_type, entity_id)
           VALUES (?,?,?,?,?)""",
        (ActorType.ENGINE.value, action, details, entity_type, entity_id),
    )
    db.connect().commit()
