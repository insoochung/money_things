"""Signal generation with gate-based blocking and thesis-driven confidence.

Philosophy: thesis conviction (set by LLM reasoning via /think sessions) IS the
confidence score. Deterministic math only handles gate checks — conviction
threshold, research maturity, earnings blackout, trading windows, risk limits.
No weighted factor scoring. Fewer trades, slow to act.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from db.database import Database
from engine import Signal, SignalAction, SignalSource, SignalStatus, ThesisStatus
from engine.earnings_calendar import is_earnings_imminent
from engine.risk import RiskManager
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

logger = logging.getLogger(__name__)

_BUY_STATUSES = {
    ThesisStatus.ACTIVE,
    ThesisStatus.STRENGTHENING,
    ThesisStatus.CONFIRMED,
}

_SELL_STATUSES = {
    ThesisStatus.WEAKENING,
    ThesisStatus.INVALIDATED,
}

# Gate thresholds
_MIN_CONVICTION = 0.70
_MIN_THINK_SESSIONS = 2
_MIN_THESIS_AGE_DAYS = 7

# Position sizing
_BASE_POSITION_SIZE = 0.02
_DEFAULT_USER_ID = 1


class GateResult(BaseModel):
    """Result of gate checks — pass/fail with reason."""

    passed: bool = True
    reason: str = ""


class SignalGenerator:
    """Generate trading signals from thesis conviction with gate checks.

    Gates (BUY only — SELLs bypass):
    - Conviction ≥ 70%
    - ≥ 2 /think sessions
    - Thesis age ≥ 7 days
    - No imminent earnings
    - Not in trading window blackout
    - Risk manager pre-trade check
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
        """Scan theses and generate trading signals."""
        logger.info("signal_scan: starting")
        generated: list[dict] = []

        theses = [
            t for t in self.thesis_engine.list_theses()
            if t.status != ThesisStatus.ARCHIVED
        ]
        if not theses:
            logger.info("signal_scan: no active theses")
            return generated

        for thesis in theses:
            try:
                generated.extend(self._evaluate_thesis(thesis))
            except Exception:
                logger.exception("signal_scan: error on thesis %d", thesis.id)

        logger.info("signal_scan: generated %d signals", len(generated))
        return generated

    def _evaluate_thesis(self, thesis: Any) -> list[dict]:
        """Evaluate a single thesis across its symbols."""
        if not thesis.symbols:
            return []

        results: list[dict] = []
        held = self._get_held_symbols()
        pending = self._get_pending_signal_map()

        for symbol in thesis.symbols:
            existing_id = pending.get(symbol)

            if thesis.status in _BUY_STATUSES and symbol not in held:
                result = self._try_buy(symbol, thesis, existing_id)
                if result:
                    results.append(result)

            elif thesis.status in _SELL_STATUSES and symbol in held:
                result = self._try_sell(symbol, thesis, existing_id)
                if result:
                    results.append(result)

        return results

    def _try_buy(
        self, symbol: str, thesis: Any, pending_id: int | None,
    ) -> dict | None:
        """Attempt to generate a BUY signal, checking all gates."""
        gate = self._check_gates(symbol, thesis)
        if not gate.passed:
            logger.info("signal_scan: BUY %s blocked: %s", symbol, gate.reason)
            return None

        confidence = thesis.conviction if thesis.conviction else 0.5
        reasoning = self._build_reasoning("BUY", symbol, thesis)
        return self._create_or_update_signal(
            "BUY", symbol, thesis, confidence, reasoning, pending_id,
        )

    def _try_sell(
        self, symbol: str, thesis: Any, pending_id: int | None,
    ) -> dict | None:
        """Generate a SELL signal — bypasses maturity gates."""
        confidence = 1.0 - (thesis.conviction if thesis.conviction else 0.5)
        reasoning = self._build_reasoning("SELL", symbol, thesis)
        return self._create_or_update_signal(
            "SELL", symbol, thesis, max(confidence, 0.5), reasoning, pending_id,
        )

    # --- Gates ---

    def _check_gates(self, symbol: str, thesis: Any) -> GateResult:
        """Run all deterministic gate checks for BUY signals."""
        # Conviction gate
        conviction = thesis.conviction if thesis.conviction else 0.0
        if conviction < _MIN_CONVICTION:
            return GateResult(
                passed=False,
                reason=f"conviction {conviction:.0%} < {_MIN_CONVICTION:.0%}",
            )

        # Thesis age gate
        age_result = self._check_thesis_age(thesis)
        if not age_result.passed:
            return age_result

        # Think sessions gate
        sessions = self._get_think_session_count(thesis.id)
        if sessions < _MIN_THINK_SESSIONS:
            return GateResult(
                passed=False,
                reason=f"{sessions} /think sessions < {_MIN_THINK_SESSIONS} minimum",
            )

        # Earnings blackout
        if is_earnings_imminent(symbol):
            return GateResult(
                passed=False,
                reason=f"{symbol} earnings imminent",
            )

        # Trading window blackout
        if self._is_in_trading_blackout(symbol):
            return GateResult(
                passed=False,
                reason=f"{symbol} in trading window blackout",
            )

        return GateResult()

    def _check_thesis_age(self, thesis: Any) -> GateResult:
        """Check thesis is old enough (≥ 7 days)."""
        if not thesis.created_at:
            return GateResult()

        try:
            created = datetime.fromisoformat(str(thesis.created_at))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age = datetime.now(UTC) - created
            if age < timedelta(days=_MIN_THESIS_AGE_DAYS):
                return GateResult(
                    passed=False,
                    reason=f"thesis age {age.days}d < {_MIN_THESIS_AGE_DAYS}d minimum",
                )
        except (ValueError, TypeError):
            pass

        return GateResult()

    def _get_think_session_count(self, thesis_id: int) -> int:
        """Count /think sessions (thesis_versions as proxy)."""
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM thesis_versions WHERE thesis_id = ?",
            (thesis_id,),
        )
        return row["cnt"] if row else 0

    def _is_in_trading_blackout(self, symbol: str) -> bool:
        """Check if symbol has defined trading windows but none currently open."""
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        open_window = self.db.fetchone(
            """SELECT id FROM trading_windows
               WHERE symbol = ? AND opens IS NOT NULL AND closes IS NOT NULL
                 AND ? >= opens AND ? <= closes""",
            (symbol.upper(), now, now),
        )
        has_any = self.db.fetchone(
            "SELECT id FROM trading_windows WHERE symbol = ?",
            (symbol.upper(),),
        )
        return bool(has_any and not open_window)

    # --- Signal creation ---

    def _create_or_update_signal(
        self,
        action: str,
        symbol: str,
        thesis: Any,
        raw_confidence: float,
        reasoning: str,
        pending_id: int | None,
    ) -> dict | None:
        """Score confidence, check risk, create or update signal."""
        confidence = self.signal_engine.score_confidence(
            raw_confidence=raw_confidence,
            thesis_status=thesis.status.value,
            source_type=SignalSource.THESIS_UPDATE.value,
        )
        if confidence < 0.3:
            logger.debug("signal_scan: %s %s confidence %.2f too low", action, symbol, confidence)
            return None

        nav = self.risk_manager._get_nav()
        size_pct = self._compute_position_size(confidence, nav)

        signal = Signal(
            action=SignalAction.BUY if action == "BUY" else SignalAction.SELL,
            symbol=symbol,
            thesis_id=thesis.id,
            confidence=round(confidence, 4),
            source=SignalSource.THESIS_UPDATE,
            horizon=thesis.horizon or "",
            reasoning=reasoning,
            size_pct=size_pct,
            status=SignalStatus.PENDING,
        )

        risk_result = self.risk_manager.pre_trade_check(signal)
        if not risk_result:
            logger.info(
                "signal_scan: %s %s blocked by risk: %s",
                action, symbol, risk_result.reason,
            )
            return None

        if pending_id:
            self._update_pending_signal(pending_id, confidence, reasoning, action)
            signal.id = pending_id
            return self._signal_result(
                signal, action, symbol, confidence, size_pct, reasoning,
                updated=True,
            )

        created = self.signal_engine.create_signal(signal)
        return self._signal_result(created, action, symbol, confidence, size_pct, reasoning)

    def _compute_position_size(self, confidence: float, nav: float) -> float:
        """Size = base (2%) × confidence × 2, capped at max_position_pct."""
        if nav <= 0:
            return _BASE_POSITION_SIZE
        size = _BASE_POSITION_SIZE * confidence * 2
        max_pct = self.risk_manager._get_limit("max_position_pct", 0.15)
        return min(size, max_pct)

    def _signal_result(
        self,
        signal: Signal,
        action: str,
        symbol: str,
        confidence: float,
        size_pct: float,
        reasoning: str,
        *,
        updated: bool = False,
    ) -> dict:
        """Build result dict for a generated signal."""
        logger.info(
            "signal_scan: %s %s signal for %s (confidence=%.2f)",
            "updated" if updated else "created", action, symbol, confidence,
        )
        result = {
            "signal_id": signal.id,
            "action": action,
            "symbol": symbol,
            "confidence": confidence,
            "size_pct": size_pct,
            "thesis_id": signal.thesis_id,
            "reasoning": reasoning,
        }
        if updated:
            result["updated"] = True
        return result

    # --- Reasoning ---

    def _build_reasoning(self, action: str, symbol: str, thesis: Any) -> str:
        """Build human-readable reasoning string."""
        parts = [f"Thesis '{thesis.title}' ({thesis.status.value})"]

        if action == "BUY":
            parts.append(f"{symbol} not yet in portfolio")
        else:
            parts.append(f"{symbol} held — thesis {thesis.status.value}")

        conviction = thesis.conviction if thesis.conviction else 0
        parts.append(f"conviction {conviction:.0%}")

        if thesis.horizon:
            parts.append(f"horizon: {thesis.horizon}")

        return ". ".join(parts) + "."

    # --- Helpers ---

    def _get_held_symbols(self) -> set[str]:
        rows = self.db.fetchall(
            "SELECT DISTINCT symbol FROM positions WHERE shares > 0",
        )
        return {r["symbol"] for r in rows}

    def _get_pending_signal_map(self) -> dict[str, int]:
        """Map of symbol → signal ID for pending signals (dedup)."""
        rows = self.db.fetchall(
            "SELECT id, symbol FROM signals WHERE status = ? ORDER BY created_at DESC",
            (SignalStatus.PENDING.value,),
        )
        result: dict[str, int] = {}
        for r in rows:
            if r["symbol"] not in result:
                result[r["symbol"]] = r["id"]
        return result

    def _update_pending_signal(
        self, signal_id: int, confidence: float, reasoning: str, action: str,
    ) -> None:
        """Update existing pending signal with fresh analysis."""
        self.db.execute(
            """UPDATE signals SET confidence = ?, reasoning = ?, action = ?,
               created_at = datetime('now') WHERE id = ? AND status = 'pending'""",
            (confidence, reasoning, action, signal_id),
        )
        self.db.connect().commit()
