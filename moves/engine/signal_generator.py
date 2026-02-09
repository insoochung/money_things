"""Signal generation pipeline: autonomous thesis evaluation and signal creation.

Evaluates active theses against current market data and generates actionable
trading signals. This is the "brain" that connects theses to signals — the loop:
thesis → check market data → score → generate signal.

Classes:
    SignalGenerator: Evaluates theses and generates trading signals.
"""

from __future__ import annotations

import logging
from typing import Any

from db.database import Database
from engine import Signal, SignalAction, SignalSource, SignalStatus, ThesisStatus
from engine.risk import RiskManager
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

logger = logging.getLogger(__name__)

# Thesis statuses that can generate BUY signals
_BUY_STATUSES = {
    ThesisStatus.ACTIVE,
    ThesisStatus.STRENGTHENING,
    ThesisStatus.CONFIRMED,
}

# Thesis statuses that should generate SELL signals for held positions
_SELL_STATUSES = {
    ThesisStatus.WEAKENING,
    ThesisStatus.INVALIDATED,
}

# Minimum daily price move (%) to trigger a price-based signal
_DAILY_MOVE_THRESHOLD = 2.0
# Minimum weekly price move (%) to trigger a price-based signal
_WEEKLY_MOVE_THRESHOLD = 5.0
# Base position size as fraction of NAV
_BASE_POSITION_SIZE = 0.02
# Default user_id for single-user system
_DEFAULT_USER_ID = 1


class SignalGenerator:
    """Generates trading signals by evaluating theses against market data.

    Scans all active/non-archived theses, checks each symbol for price movements
    and portfolio state, then generates BUY or SELL signals with proper confidence
    scoring and risk checks.

    Attributes:
        db: Database instance.
        signal_engine: For creating and listing signals.
        thesis_engine: For listing theses.
        risk_manager: For pre-trade risk checks and NAV.
        pricing: The engine.pricing module for fetching prices.
        user_id: User ID for risk checks (default 1).
    """

    def __init__(
        self,
        db: Database,
        signal_engine: SignalEngine,
        thesis_engine: ThesisEngine,
        risk_manager: RiskManager,
        pricing: Any,
        user_id: int = _DEFAULT_USER_ID,
    ) -> None:
        self.db = db
        self.signal_engine = signal_engine
        self.thesis_engine = thesis_engine
        self.risk_manager = risk_manager
        self.pricing = pricing
        self.user_id = user_id

    def run_scan(self) -> list[dict]:
        """Main scan loop. Called by scheduler.

        For each non-archived thesis:
        1. Evaluate thesis symbols for potential signals
        2. Score confidence using the full pipeline
        3. Run pre-trade risk checks
        4. Create signals that pass all checks

        Returns:
            List of dicts describing generated signals.
        """
        logger.info("signal_scan: starting scan")
        generated: list[dict] = []

        # Get all non-archived theses
        all_theses = self.thesis_engine.list_theses()
        active_theses = [
            t for t in all_theses
            if t.status != ThesisStatus.ARCHIVED
        ]

        if not active_theses:
            logger.info("signal_scan: no active theses to evaluate")
            return generated

        for thesis in active_theses:
            try:
                signals = self._evaluate_thesis_symbols(thesis)
                generated.extend(signals)
            except Exception:
                logger.exception(
                    "signal_scan: error evaluating thesis %d (%s)",
                    thesis.id,
                    thesis.title,
                )

        logger.info("signal_scan: generated %d signals", len(generated))
        return generated

    def _evaluate_thesis_symbols(self, thesis: Any) -> list[dict]:
        """Evaluate each symbol in a thesis for potential signals.

        Args:
            thesis: Thesis model from ThesisEngine.

        Returns:
            List of signal result dicts.
        """
        if not thesis.symbols:
            return []

        results: list[dict] = []

        # Get current positions
        held_symbols = self._get_held_symbols()

        # Get pending signals to avoid duplicates
        pending_symbols = self._get_pending_symbols()

        for symbol in thesis.symbols:
            # Skip if there's already a pending signal for this symbol
            if symbol in pending_symbols:
                logger.debug("signal_scan: skipping %s — pending signal exists", symbol)
                continue

            # Check price data
            trigger = self._check_price_triggers(symbol, thesis)

            # Determine action based on thesis status and holdings
            if thesis.status in _BUY_STATUSES and symbol not in held_symbols:
                # Potential BUY: thesis is positive and we don't hold it
                if trigger or thesis.status in {ThesisStatus.STRENGTHENING, ThesisStatus.CONFIRMED}:
                    # Generate BUY if there's a price trigger OR thesis is strong
                    reasoning = self._build_reasoning("BUY", symbol, thesis, trigger)
                    raw_confidence = self._compute_raw_confidence(thesis, trigger)
                    result = self._generate_signal(
                        "BUY", symbol, thesis, raw_confidence, reasoning,
                    )
                    if result:
                        results.append(result)

            elif thesis.status in _SELL_STATUSES and symbol in held_symbols:
                # Potential SELL: thesis is negative and we hold it
                reasoning = self._build_reasoning("SELL", symbol, thesis, trigger)
                raw_confidence = self._compute_raw_confidence(thesis, trigger)
                result = self._generate_signal(
                    "SELL", symbol, thesis, raw_confidence, reasoning,
                )
                if result:
                    results.append(result)

        return results

    def _check_price_triggers(self, symbol: str, thesis: Any) -> dict | None:
        """Check if price has hit significant movement thresholds.

        Args:
            symbol: Ticker symbol.
            thesis: Thesis model (for context).

        Returns:
            Dict with trigger info if significant move detected, None otherwise.
        """
        try:
            price_data = self.pricing.get_price(symbol, db=self.db)
        except Exception:
            logger.warning("signal_scan: price fetch failed for %s", symbol)
            return None

        if "error" in price_data or not price_data.get("price"):
            return None

        change_pct = price_data.get("change_percent")
        if change_pct is None:
            return None

        abs_change = abs(change_pct)

        if abs_change >= _DAILY_MOVE_THRESHOLD:
            return {
                "type": "daily_move",
                "price": price_data["price"],
                "change_percent": change_pct,
                "direction": "up" if change_pct > 0 else "down",
            }

        # Check weekly move via history
        try:
            history = self.pricing.get_history(symbol, period="5d", db=self.db)
            if history and len(history) >= 2:
                start_price = history[0]["close"]
                end_price = history[-1]["close"]
                weekly_change = ((end_price - start_price) / start_price) * 100
                if abs(weekly_change) >= _WEEKLY_MOVE_THRESHOLD:
                    return {
                        "type": "weekly_move",
                        "price": price_data["price"],
                        "change_percent": round(weekly_change, 2),
                        "direction": "up" if weekly_change > 0 else "down",
                    }
        except Exception:
            pass

        return None

    def _generate_signal(
        self,
        action: str,
        symbol: str,
        thesis: Any,
        raw_confidence: float,
        reasoning: str,
    ) -> dict | None:
        """Create a signal through the signal engine after scoring and risk check.

        Args:
            action: "BUY" or "SELL".
            symbol: Ticker symbol.
            thesis: Thesis model.
            raw_confidence: Unscored confidence 0.0–1.0.
            reasoning: Human-readable reasoning string.

        Returns:
            Dict describing the created signal, or None if blocked.
        """
        # Score confidence through the full pipeline
        confidence = self.signal_engine.score_confidence(
            raw_confidence=raw_confidence,
            thesis_status=thesis.status.value,
            source_type=SignalSource.THESIS_UPDATE.value,
        )

        # Skip very low confidence signals
        if confidence < 0.3:
            logger.debug(
                "signal_scan: skipping %s %s — confidence %.2f too low",
                action, symbol, confidence,
            )
            return None

        # Compute position size
        nav = self.risk_manager._get_nav()
        size_pct = self._compute_position_size(symbol, confidence, nav)

        signal_action = SignalAction.BUY if action == "BUY" else SignalAction.SELL

        signal = Signal(
            action=signal_action,
            symbol=symbol,
            thesis_id=thesis.id,
            confidence=round(confidence, 4),
            source=SignalSource.THESIS_UPDATE,
            horizon=thesis.horizon or "",
            reasoning=reasoning,
            size_pct=size_pct,
            status=SignalStatus.PENDING,
        )

        # Run pre-trade risk check
        risk_result = self.risk_manager.pre_trade_check(signal)
        if not risk_result:
            logger.info(
                "signal_scan: %s %s blocked by risk: %s",
                action, symbol, risk_result.reason,
            )
            return None

        # Create the signal
        created = self.signal_engine.create_signal(signal)
        logger.info(
            "signal_scan: created %s signal for %s (confidence=%.2f, thesis=%d)",
            action, symbol, confidence, thesis.id,
        )
        return {
            "signal_id": created.id,
            "action": action,
            "symbol": symbol,
            "confidence": confidence,
            "size_pct": size_pct,
            "thesis_id": thesis.id,
            "reasoning": reasoning,
        }

    def _compute_position_size(
        self, symbol: str, confidence: float, nav: float
    ) -> float:
        """Compute suggested position size as fraction of NAV.

        Base: 2% of NAV.
        Scale by confidence: size = base * confidence * 2.
        Cap at max_position_pct from risk limits.

        Args:
            symbol: Ticker symbol (for future per-symbol adjustments).
            confidence: Scored confidence 0.0–1.0.
            nav: Current portfolio NAV.

        Returns:
            Position size as fraction of NAV (e.g., 0.03 = 3%).
        """
        if nav <= 0:
            return _BASE_POSITION_SIZE

        size = _BASE_POSITION_SIZE * confidence * 2
        # Cap at risk limit
        max_pct = self.risk_manager._get_limit(
            "max_position_pct", 0.15
        )
        return min(size, max_pct)

    def _compute_raw_confidence(self, thesis: Any, trigger: dict | None) -> float:
        """Compute raw (pre-scoring) confidence based on thesis and trigger.

        Args:
            thesis: Thesis model.
            trigger: Price trigger dict or None.

        Returns:
            Raw confidence 0.0–1.0.
        """
        # Start with thesis conviction
        base = thesis.conviction if thesis.conviction else 0.5

        # Boost if there's a price trigger
        if trigger:
            abs_move = abs(trigger.get("change_percent", 0))
            # Bigger moves = more confidence (up to +0.15)
            trigger_boost = min(abs_move / 20.0, 0.15)
            base = min(base + trigger_boost, 1.0)

        return base

    def _build_reasoning(
        self, action: str, symbol: str, thesis: Any, trigger: dict | None
    ) -> str:
        """Build human-readable reasoning for a signal.

        Args:
            action: "BUY" or "SELL".
            symbol: Ticker symbol.
            thesis: Thesis model.
            trigger: Price trigger dict or None.

        Returns:
            Reasoning string.
        """
        parts = [f"Thesis '{thesis.title}' ({thesis.status.value})"]

        if action == "BUY":
            parts.append(f"{symbol} not yet in portfolio")
        else:
            parts.append(f"{symbol} held — thesis {thesis.status.value}")

        if trigger:
            parts.append(
                f"Price {trigger['direction']} {abs(trigger['change_percent']):.1f}% "
                f"({trigger['type']})"
            )

        return ". ".join(parts) + "."

    def _get_held_symbols(self) -> set[str]:
        """Get set of currently held symbols.

        Returns:
            Set of symbol strings with open positions.
        """
        rows = self.db.fetchall(
            "SELECT DISTINCT symbol FROM positions WHERE shares > 0"
        )
        return {r["symbol"] for r in rows}

    def _get_pending_symbols(self) -> set[str]:
        """Get set of symbols with pending signals.

        Returns:
            Set of symbol strings that already have a pending signal.
        """
        rows = self.db.fetchall(
            "SELECT DISTINCT symbol FROM signals WHERE status = ?",
            (SignalStatus.PENDING.value,),
        )
        return {r["symbol"] for r in rows}
