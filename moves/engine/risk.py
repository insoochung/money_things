"""Risk management engine: pre-trade checks, kill switch, exposure, and drawdown.

This module implements the risk management layer for money_moves. Every signal must
pass a battery of risk checks before it can be approved and executed. The risk manager
enforces portfolio-level constraints to prevent excessive concentration, overleveraging,
and trading during restricted windows.

The risk management philosophy is "defense in depth" -- multiple independent checks
that each validate a different aspect of portfolio risk. If ANY check fails, the
entire pre-trade check fails and the signal is not executed.

Risk checks performed (in order):
    1. Kill switch: Is emergency trading halt active?
    2. Position size: Would this trade exceed max single-position allocation?
    3. Sector concentration: Would this trade over-concentrate in one sector?
    4. Gross exposure: Would total exposure (long + short) exceed the limit?
    5. Net exposure: Would net exposure (long - short) fall outside the allowed band?
    6. Trading window: Is trading allowed for this symbol right now? (META-specific)
    7. Drawdown limit: Has portfolio drawdown from peak exceeded the limit?
    8. Daily loss limit: Has today's loss exceeded the daily limit?

Risk limits are stored in the risk_limits table and can be configured per limit type.
Default values are used when no database entry exists.

The kill switch is a global emergency halt that blocks ALL trading. It can be
activated by the user (via Telegram or dashboard) or automatically by the system
(e.g., when drawdown exceeds a critical threshold). The kill switch state is
persisted in the kill_switch table with activation/deactivation timestamps and reasons.

Classes:
    RiskCheckResult: Simple result object with passed/failed status and reason.
    RiskManager: Main risk management class with all pre-trade checks.

Functions:
    _audit: Helper to create audit log entries for risk actions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from db.database import Database
from engine import ActorType, Signal, SignalAction

logger = logging.getLogger(__name__)


class RiskCheckResult:
    """Result of a single risk check.

    A simple value object that wraps a boolean pass/fail result with an optional
    reason string explaining why the check failed. Supports boolean evaluation
    via __bool__ so it can be used directly in if-statements.

    Attributes:
        passed: True if the risk check passed, False if it failed.
        reason: Human-readable explanation of why the check failed (empty if passed).

    Examples:
        >>> result = RiskCheckResult(True)
        >>> if result:
        ...     print("Check passed")

        >>> result = RiskCheckResult(False, "Position too large")
        >>> if not result:
        ...     print(f"Failed: {result.reason}")
    """

    def __init__(self, passed: bool, reason: str = "") -> None:
        """Initialize a RiskCheckResult.

        Args:
            passed: Whether the risk check passed (True) or failed (False).
            reason: Human-readable reason for failure. Should be empty when passed=True.
        """
        self.passed = passed
        self.reason = reason

    def __bool__(self) -> bool:
        """Allow RiskCheckResult to be used directly in boolean contexts.

        Returns:
            True if the check passed, False if it failed.
        """
        return self.passed


class RiskManager:
    """Portfolio risk management engine.

    Provides pre-trade risk validation, kill switch management, exposure calculation,
    and drawdown monitoring. All risk limits are read from the risk_limits table in
    the database, with sensible defaults if no entry exists.

    The RiskManager operates on the current state of the portfolio (positions,
    portfolio_value, trading_windows tables) and does not maintain any in-memory
    state beyond the database connection.

    Attributes:
        db: Database instance used for reading portfolio state and risk limits,
            and writing audit log entries and kill switch state changes.
    """

    def __init__(self, db: Database) -> None:
        """Initialize the RiskManager with a database connection.

        Args:
            db: Database instance for reading portfolio state and writing risk events.
        """
        self.db = db

    def pre_trade_check(self, signal: Signal) -> RiskCheckResult:
        """Run all pre-trade risk checks against a signal. Returns first failure or pass.

        Executes each risk check in sequence. If any check fails, the failed result
        is immediately returned (with an audit log entry) and subsequent checks are
        skipped. If all checks pass, returns a passing result.

        The check order matters: kill switch is checked first (cheapest, most severe),
        followed by position-level checks, then portfolio-level checks.

        Args:
            signal: The Signal object to validate. Must have at minimum:
                symbol, action, and optionally size_pct for position sizing checks.

        Returns:
            RiskCheckResult with passed=True if all checks pass, or passed=False
            with the reason from the first failing check.

        Side effects:
            - If a check fails, an audit_log entry is created with action
              'risk_check_failed' and the failure reason.
        """
        checks = [
            self.check_kill_switch(),
            self.check_position_size(signal),
            self.check_sector_concentration(signal),
            self.check_gross_exposure(),
            self.check_net_exposure(),
            self.check_trading_window(signal),
            self.check_drawdown_limit(),
            self.check_daily_loss_limit(),
        ]
        for check in checks:
            if not check.passed:
                _audit(self.db, "risk_check_failed", "signal", signal.id, check.reason)
                return check
        return RiskCheckResult(True)

    def check_kill_switch(self) -> RiskCheckResult:
        """Check if the global kill switch is active.

        The kill switch is an emergency trading halt that blocks ALL trades regardless
        of other risk parameters. It is stored in the kill_switch table and can be
        activated manually or automatically.

        Returns:
            RiskCheckResult with passed=False if the kill switch is active,
            passed=True otherwise.
        """
        row = self.db.fetchone("SELECT active FROM kill_switch ORDER BY id DESC LIMIT 1")
        if row and row["active"]:
            return RiskCheckResult(False, "Kill switch is active - trading halted")
        return RiskCheckResult(True)

    def check_position_size(self, signal: Signal) -> RiskCheckResult:
        """Check if the signal would cause a single position to exceed the maximum allocation.

        Calculates the current position value for the signal's symbol and adds the
        proposed new allocation (signal.size_pct * NAV). If the resulting position
        would exceed the max_position_pct limit (default 15% of NAV), the check fails.

        Args:
            signal: The Signal to validate. Uses signal.symbol to look up the current
                position and signal.size_pct to estimate the new allocation.

        Returns:
            RiskCheckResult. Passes if NAV is zero (no portfolio to check against),
            if the signal has no size_pct, or if the resulting position stays within limits.
            Fails with a descriptive message if the position would be too large.
        """
        limit = self._get_limit("max_position_pct", 0.15)
        nav = self._get_nav()
        if nav <= 0:
            return RiskCheckResult(True)

        # Get current position value for this symbol
        pos = self.db.fetchone(
            "SELECT shares, avg_cost FROM positions WHERE symbol = ?",
            (signal.symbol,),
        )
        current_value = (pos["shares"] * pos["avg_cost"]) if pos else 0

        # Estimate new position size
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

    def check_sector_concentration(self, signal: Signal) -> RiskCheckResult:
        """Check if the signal would over-concentrate the portfolio in one sector.

        Currently a pass-through that always returns True. Sector data is not available
        in mock mode (would require fetching sector info from yfinance for each position).
        This check will be implemented when live mode with real sector data is available.

        Args:
            signal: The Signal to validate (currently unused).

        Returns:
            RiskCheckResult with passed=True (always passes in current implementation).
        """
        # In mock mode without sector data, always pass
        return RiskCheckResult(True)

    def check_gross_exposure(self) -> RiskCheckResult:
        """Check if total gross exposure (long + short) exceeds the portfolio limit.

        Gross exposure measures the total market exposure regardless of direction.
        A portfolio with $100k long and $50k short has 150% gross exposure on a $100k NAV.
        This prevents over-leveraging.

        Returns:
            RiskCheckResult. Fails if gross_pct exceeds max_gross_exposure (default 150%).
        """
        limit = self._get_limit("max_gross_exposure", 1.50)
        exposure = self.calculate_exposure()
        if exposure["gross_pct"] > limit:
            return RiskCheckResult(
                False,
                f"Gross exposure {exposure['gross_pct']:.1%} exceeds limit {limit:.0%}",
            )
        return RiskCheckResult(True)

    def check_net_exposure(self) -> RiskCheckResult:
        """Check if net exposure (long - short) falls within the allowed band.

        Net exposure measures the portfolio's directional bias. A fully long portfolio
        has 100% net exposure. The allowed band (default -30% to 130%) prevents extreme
        directional bets in either direction.

        Returns:
            RiskCheckResult. Fails if net_pct is outside [net_exposure_min, net_exposure_max].
        """
        net_min = self._get_limit("net_exposure_min", -0.30)
        net_max = self._get_limit("net_exposure_max", 1.30)
        exposure = self.calculate_exposure()
        net = exposure["net_pct"]
        if net < net_min or net > net_max:
            return RiskCheckResult(
                False,
                f"Net exposure {net:.1%} outside band [{net_min:.0%}, {net_max:.0%}]",
            )
        return RiskCheckResult(True)

    def check_trading_window(self, signal: Signal) -> RiskCheckResult:
        """Check META-specific trading window restrictions.

        Meta (Facebook) employees have trading restrictions tied to quarterly earnings
        windows. This check blocks BUY signals for META when the trading window is
        closed, but allows SELL signals (to enable exiting positions even during
        blackout periods).

        Non-META symbols always pass this check.

        The trading_windows table stores date ranges when trading is permitted for
        restricted symbols.

        Args:
            signal: The Signal to validate. Only checks META symbols; all others pass.

        Returns:
            RiskCheckResult. Fails for META BUY signals when no active trading window
            exists. Always passes for non-META symbols and for META SELL signals.
        """
        if signal.symbol != "META":
            return RiskCheckResult(True)

        now = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.db.fetchone(
            """SELECT * FROM trading_windows
               WHERE symbol = 'META' AND opens <= ? AND closes >= ?""",
            (now, now),
        )
        if not row:
            # If selling from existing position, allow it
            if signal.action == SignalAction.SELL:
                return RiskCheckResult(True)
            return RiskCheckResult(False, "META trading window is closed")
        return RiskCheckResult(True)

    def check_drawdown_limit(self) -> RiskCheckResult:
        """Check if portfolio drawdown from peak NAV exceeds the maximum allowed.

        Drawdown measures the decline from the portfolio's all-time high value. If the
        current drawdown exceeds max_drawdown (default 20%), all trading is halted
        to prevent further losses.

        Returns:
            RiskCheckResult. Fails if current drawdown exceeds the max_drawdown limit.
        """
        limit = self._get_limit("max_drawdown", 0.20)
        dd = self.current_drawdown()
        if dd > limit:
            return RiskCheckResult(
                False,
                f"Drawdown {dd:.1%} exceeds limit {limit:.0%} - trading halted",
            )
        return RiskCheckResult(True)

    def check_daily_loss_limit(self) -> RiskCheckResult:
        """Check if today's portfolio loss exceeds the daily loss limit.

        Reads today's daily_return_pct from the portfolio_value table. If the daily
        return is more negative than the daily_loss_limit (default -3%), trading is
        halted for the rest of the day.

        Returns:
            RiskCheckResult. Fails if today's loss exceeds the daily loss limit.
            Passes if no portfolio_value entry exists for today.
        """
        limit = self._get_limit("daily_loss_limit", 0.03)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        pv = self.db.fetchone(
            "SELECT daily_return_pct FROM portfolio_value WHERE date = ?", (today,)
        )
        if pv and pv["daily_return_pct"] is not None:
            if pv["daily_return_pct"] < -limit:
                return RiskCheckResult(
                    False,
                    f"Daily loss {pv['daily_return_pct']:.1%} exceeds limit {limit:.0%}",
                )
        return RiskCheckResult(True)

    # --- Kill Switch ---

    def activate_kill_switch(self, reason: str, activated_by: str = "user") -> None:
        """Activate the global kill switch to halt all trading.

        Creates a new kill_switch record with active=TRUE. This immediately blocks
        all pre-trade checks until the kill switch is deactivated.

        Args:
            reason: Human-readable reason for activation (e.g., 'Market crash',
                'System error detected').
            activated_by: Who activated the kill switch (e.g., 'user', 'system',
                'risk_manager'). Defaults to 'user'.

        Side effects:
            - Inserts a new row into the kill_switch table with active=TRUE.
            - Inserts an audit_log entry with action 'kill_switch_activated'.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """INSERT INTO kill_switch (active, activated_at, reason, activated_by)
               VALUES (TRUE, ?, ?, ?)""",
            (now, reason, activated_by),
        )
        self.db.connect().commit()
        _audit(self.db, "kill_switch_activated", "kill_switch", None, reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the global kill switch to resume trading.

        Finds the most recent active kill_switch record and sets active=FALSE with
        a deactivation timestamp. Trading is immediately allowed again after this.

        Side effects:
            - Updates the most recent active kill_switch row to active=FALSE.
            - Inserts an audit_log entry with action 'kill_switch_deactivated'.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        row = self.db.fetchone(
            "SELECT id FROM kill_switch WHERE active = TRUE ORDER BY id DESC LIMIT 1"
        )
        if row:
            self.db.execute(
                "UPDATE kill_switch SET active = FALSE, deactivated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            self.db.connect().commit()
        _audit(self.db, "kill_switch_deactivated", "kill_switch", None)

    def is_kill_switch_active(self) -> bool:
        """Check whether the global kill switch is currently active.

        Returns:
            True if there is an active kill_switch record, False otherwise.
        """
        row = self.db.fetchone(
            "SELECT active FROM kill_switch WHERE active = TRUE ORDER BY id DESC LIMIT 1"
        )
        return bool(row)

    # --- Exposure ---

    def calculate_exposure(self) -> dict:
        """Calculate current portfolio exposure metrics.

        Computes long, short, gross, and net exposure values and percentages based
        on current positions and portfolio NAV. All calculations use shares * avg_cost
        as position value (mark-to-market would use current prices instead).

        Returns:
            Dictionary with exposure metrics:
                - long_value (float): Total value of long positions
                - short_value (float): Total value of short positions
                - gross_value (float): long_value + short_value (total market exposure)
                - net_value (float): long_value - short_value (directional exposure)
                - gross_pct (float): gross_value / NAV (e.g., 1.5 = 150%)
                - net_pct (float): net_value / NAV (e.g., 1.0 = 100% long)
                - long_pct (float): long_value / NAV
                - short_pct (float): short_value / NAV
            Returns all zeros if NAV is zero or negative.
        """
        nav = self._get_nav()
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

        positions = self.db.fetchall("SELECT * FROM positions WHERE shares > 0")
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

    def current_drawdown(self) -> float:
        """Calculate the current drawdown from peak portfolio NAV.

        Scans the entire portfolio_value history to find the peak total_value,
        then calculates the percentage decline from that peak to the most recent
        value. A drawdown of 0.10 means the portfolio is 10% below its all-time high.

        Returns:
            Float between 0.0 and 1.0 representing the drawdown percentage.
            Returns 0.0 if there is no portfolio history or peak is zero.
        """
        rows = self.db.fetchall("SELECT total_value FROM portfolio_value ORDER BY date")
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

    def _get_nav(self) -> float:
        """Get the most recent portfolio net asset value (NAV).

        Reads the total_value from the most recent portfolio_value record.

        Returns:
            The most recent total_value, or 0.0 if no portfolio_value records exist.
        """
        pv = self.db.fetchone("SELECT total_value FROM portfolio_value ORDER BY date DESC LIMIT 1")
        return pv["total_value"] if pv else 0.0

    def _get_limit(self, limit_type: str, default: float) -> float:
        """Get a risk limit value from the database, or return the default.

        Reads from the risk_limits table. Only returns the database value if the
        limit is enabled (enabled=TRUE). Otherwise returns the default.

        Args:
            limit_type: The limit type key (e.g., 'max_position_pct', 'max_drawdown').
            default: The default value to use if the limit is not found or not enabled.

        Returns:
            The configured limit value, or the default if not found/not enabled.
        """
        row = self.db.fetchone(
            "SELECT value, enabled FROM risk_limits WHERE limit_type = ?",
            (limit_type,),
        )
        if row and row["enabled"]:
            return row["value"]
        return default


def _audit(
    db: Database, action: str, entity_type: str, entity_id: int | None, details: str = ""
) -> None:
    """Create an audit log entry for a risk management action.

    Records the action in the audit_log table with the ENGINE actor type.
    This provides a complete trail of all risk events including kill switch
    activations, deactivations, and failed risk checks.

    Args:
        db: Database instance for writing the audit entry.
        action: The action performed (e.g., 'risk_check_failed', 'kill_switch_activated').
        entity_type: The type of entity affected (e.g., 'signal', 'kill_switch').
        entity_id: The database ID of the affected entity (None for system-wide actions).
        details: Additional context (e.g., the risk check failure reason).

    Side effects:
        - Inserts a row into the audit_log table.
        - Commits the database transaction.
    """
    db.execute(
        """INSERT INTO audit_log (actor, action, details, entity_type, entity_id)
           VALUES (?,?,?,?,?)""",
        (ActorType.ENGINE.value, action, details, entity_type, entity_id),
    )
    db.connect().commit()
