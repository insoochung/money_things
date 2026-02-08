"""Tests for the Telegram bot module.

Tests use mocked telegram API — no real API calls are made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.telegram import MoneyMovesBot, format_signal_message
from config.settings import Mode
from engine import OrderResult, OrderStatus, Signal, SignalAction, SignalSource, SignalStatus
from engine.signals import SignalEngine


def _make_signal(**overrides) -> Signal:
    """Create a test signal with sensible defaults."""
    defaults = {
        "id": 1,
        "action": SignalAction.BUY,
        "symbol": "NVDA",
        "thesis_id": 1,
        "confidence": 0.82,
        "source": SignalSource.THESIS_UPDATE,
        "horizon": "6 months",
        "reasoning": "Strong datacenter revenue growth",
        "size_pct": 3.5,
        "funding_plan": "Sell 10 shares INTC",
        "status": SignalStatus.PENDING,
        "created_at": datetime.now(UTC).isoformat(),
    }
    defaults.update(overrides)
    return Signal(**defaults)


def _make_bot(db, signal_engine=None, broker=None) -> MoneyMovesBot:
    """Create a bot with mocked dependencies."""
    se = signal_engine or MagicMock(spec=SignalEngine)
    br = broker or AsyncMock()
    bot = MoneyMovesBot(
        db=db,
        signal_engine=se,
        broker=br,
        token="fake-token",
        chat_id=12345,
        mode=Mode.MOCK,
    )
    # Mock app and bot
    bot.app = MagicMock()
    bot.app.bot = AsyncMock()
    return bot


class TestFormatSignal:
    """Test signal message formatting."""

    def test_send_signal_format(self) -> None:
        """Verify message text contains key signal info."""
        signal = _make_signal()
        text = format_signal_message(signal)

        assert "BUY" in text
        assert "NVDA" in text
        assert "82%" in text
        assert "High" in text
        assert "datacenter" in text
        assert "3.5%" in text
        assert "INTC" in text

    def test_low_confidence_label(self) -> None:
        """Low confidence signals get 'Low' label."""
        signal = _make_signal(confidence=0.3)
        text = format_signal_message(signal)
        assert "Low" in text

    def test_medium_confidence_label(self) -> None:
        """Medium confidence signals get 'Medium' label."""
        signal = _make_signal(confidence=0.65)
        text = format_signal_message(signal)
        assert "Medium" in text


class TestSendSignal:
    """Test send_signal method."""

    @pytest.mark.asyncio
    async def test_send_signal_returns_message_id(self, seeded_db) -> None:
        """send_signal sends message and returns message_id."""
        bot = _make_bot(seeded_db)
        mock_msg = MagicMock()
        mock_msg.message_id = 42
        bot.app.bot.send_message = AsyncMock(return_value=mock_msg)

        signal = _make_signal()
        msg_id = await bot.send_signal(signal)

        assert msg_id == 42
        bot.app.bot.send_message.assert_called_once()
        call_kwargs = bot.app.bot.send_message.call_args
        assert "NVDA" in call_kwargs.kwargs["text"]


class TestApproveCallback:
    """Test the approve callback handler."""

    @pytest.mark.asyncio
    async def test_approve_callback(self, seeded_db) -> None:
        """Approving a pending signal executes trade and edits message."""
        se = SignalEngine(seeded_db)
        signal = se.create_signal(_make_signal(id=None))
        broker = AsyncMock()
        broker.place_order = AsyncMock(
            return_value=OrderResult(
                order_id="ORD-1",
                status=OrderStatus.FILLED,
                filled_price=130.0,
                filled_shares=10,
                message="Filled BUY 10 NVDA @ $130.00",
            )
        )

        bot = _make_bot(seeded_db, signal_engine=se, broker=broker)
        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.data = f"approve:{signal.id}"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        await bot.handle_approve(update, None)

        broker.place_order.assert_called_once()
        update.callback_query.edit_message_text.assert_called_once()
        edit_text = update.callback_query.edit_message_text.call_args.args[0]
        assert "Approved" in edit_text
        assert "ORD-1" in edit_text

        # Signal should be approved in DB
        updated = se.get_signal(signal.id)
        assert updated.status == SignalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_approve_already_decided(self, seeded_db) -> None:
        """Approving a non-pending signal shows warning."""
        se = SignalEngine(seeded_db)
        signal = se.create_signal(_make_signal(id=None))
        se.reject_signal(signal.id)

        bot = _make_bot(seeded_db, signal_engine=se)
        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.data = f"approve:{signal.id}"
        update.callback_query.edit_message_text = AsyncMock()

        await bot.handle_approve(update, None)
        edit_text = update.callback_query.edit_message_text.call_args.args[0]
        assert "no longer pending" in edit_text


class TestRejectCallback:
    """Test the reject callback handler."""

    @pytest.mark.asyncio
    async def test_reject_callback(self, seeded_db) -> None:
        """Rejecting a pending signal updates status and edits message."""
        se = SignalEngine(seeded_db)
        signal = se.create_signal(_make_signal(id=None))

        bot = _make_bot(seeded_db, signal_engine=se)
        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.data = f"reject:{signal.id}"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        await bot.handle_reject(update, None)

        update.callback_query.edit_message_text.assert_called_once()
        edit_text = update.callback_query.edit_message_text.call_args.args[0]
        assert "Rejected" in edit_text

        updated = se.get_signal(signal.id)
        assert updated.status == SignalStatus.REJECTED


class TestExpiredSignals:
    """Test signal expiry check."""

    @pytest.mark.asyncio
    async def test_expired_signal_check(self, seeded_db) -> None:
        """Signals older than 24h are marked as ignored."""
        se = SignalEngine(seeded_db)
        signal = se.create_signal(_make_signal(id=None))

        # Manually backdate the signal
        old_time = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        seeded_db.execute("UPDATE signals SET created_at = ? WHERE id = ?", (old_time, signal.id))
        seeded_db.connect().commit()

        bot = _make_bot(seeded_db, signal_engine=se)
        expired = await bot.check_expired_signals()

        assert signal.id in expired
        updated = se.get_signal(signal.id)
        assert updated.status == SignalStatus.IGNORED

    @pytest.mark.asyncio
    async def test_recent_signal_not_expired(self, seeded_db) -> None:
        """Recent signals are not expired."""
        se = SignalEngine(seeded_db)
        signal = se.create_signal(_make_signal(id=None))

        bot = _make_bot(seeded_db, signal_engine=se)
        expired = await bot.check_expired_signals()
        assert signal.id not in expired


class TestCommands:
    """Test command handlers."""

    @pytest.mark.asyncio
    async def test_cmd_status(self, seeded_db) -> None:
        """Verify /status response contains NAV and mode info."""
        bot = _make_bot(seeded_db)
        update = MagicMock()
        update.message = AsyncMock()
        update.message.reply_text = AsyncMock()

        await bot.cmd_status(update, None)

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args.args[0]
        assert "100,000" in text
        assert "Mock" in text
        assert "OFF" in text

    @pytest.mark.asyncio
    async def test_cmd_killswitch(self, seeded_db) -> None:
        """Verify kill switch toggles state."""
        bot = _make_bot(seeded_db)
        update = MagicMock()
        update.message = AsyncMock()
        update.message.reply_text = AsyncMock()

        # Toggle ON
        await bot.cmd_killswitch(update, None)
        text = update.message.reply_text.call_args.args[0]
        assert "ON" in text

        # Toggle OFF
        await bot.cmd_killswitch(update, None)
        text = update.message.reply_text.call_args.args[0]
        assert "OFF" in text

    @pytest.mark.asyncio
    async def test_cmd_mode(self, seeded_db) -> None:
        """Verify /mode returns current mode."""
        bot = _make_bot(seeded_db)
        update = MagicMock()
        update.message = AsyncMock()
        update.message.reply_text = AsyncMock()

        await bot.cmd_mode(update, None)
        text = update.message.reply_text.call_args.args[0]
        assert "Mock" in text
