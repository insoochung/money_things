"""Telegram bot for signal approval, portfolio status, and kill switch control.

Provides inline button approval/rejection of trading signals, command handlers
for portfolio status queries, and automatic expiry of unanswered signals after 24h.

Uses python-telegram-bot v22+ async API.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from broker.base import Broker
from config.settings import Mode
from db.database import Database
from engine import Order, Signal, SignalStatus
from engine.signals import SignalEngine

logger = logging.getLogger(__name__)


def _confidence_label(confidence: float) -> str:
    """Map confidence score to human-readable label."""
    if confidence >= 0.8:
        return "High"
    if confidence >= 0.6:
        return "Medium"
    return "Low"


def format_signal_message(signal: Signal) -> str:
    """Build the Telegram notification text for a new signal.

    Args:
        signal: Signal to format.

    Returns:
        Formatted message string with signal details.
    """
    pct = round(signal.confidence * 100)
    label = _confidence_label(signal.confidence)
    size_line = f"Size: {signal.size_pct}% of NAV" if signal.size_pct else ""
    funding_line = f"Funding: {signal.funding_plan}" if signal.funding_plan else ""

    lines = [
        f"🔔 New Signal: {signal.action.value} {signal.symbol}",
        "",
        f"Confidence: {pct}% ({label})",
    ]
    if signal.reasoning:
        lines += ["", f"Reasoning: {signal.reasoning}"]
    if size_line:
        lines += ["", size_line]
    if funding_line:
        lines.append(funding_line)

    return "\n".join(lines)


def _signal_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    """Build Approve / Reject inline keyboard for a signal."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{signal_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{signal_id}"),
            ]
        ]
    )


class MoneyMovesBot:
    """Telegram bot for signal approval and portfolio status.

    Attributes:
        db: Database instance.
        signal_engine: SignalEngine for signal lifecycle operations.
        broker: Broker instance for order execution.
        token: Telegram bot API token.
        chat_id: Telegram chat ID for notifications.
        app: python-telegram-bot Application instance.
    """

    def __init__(
        self,
        db: Database,
        signal_engine: SignalEngine,
        broker: Broker,
        token: str,
        chat_id: int,
        mode: Mode = Mode.MOCK,
    ) -> None:
        self.db = db
        self.signal_engine = signal_engine
        self.broker = broker
        self.token = token
        self.chat_id = chat_id
        self.mode = mode
        self.app: Application | None = None

    async def start(self) -> None:
        """Build the application, register handlers, and send startup message."""
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler("start", self.cmd_help))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("killswitch", self.cmd_killswitch))
        self.app.add_handler(CommandHandler("mode", self.cmd_mode))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

        mode_label = "Mock" if self.mode == Mode.MOCK else "Live"
        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=f"⚡ Money Moves online ({mode_label} mode)",
        )

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    # --- Signal notifications ---

    async def send_signal(self, signal: Signal) -> int:
        """Send a signal notification with Approve/Reject buttons.

        Args:
            signal: Signal to notify about.

        Returns:
            Telegram message ID of the sent notification.
        """
        if not self.app:
            msg = "Bot not started"
            raise RuntimeError(msg)

        text = format_signal_message(signal)
        keyboard = _signal_keyboard(signal.id)
        message = await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_markup=keyboard,
        )
        return message.message_id

    # --- Callback router ---

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route inline button callbacks to approve or reject handlers."""
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        if query.data.startswith("approve:"):
            await self.handle_approve(update, context)
        elif query.data.startswith("reject:"):
            await self.handle_reject(update, context)

    # --- Approve / Reject ---

    async def handle_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Approve a pending signal and execute the trade.

        Flow: validate signal → place order → update status → edit message.
        """
        query = update.callback_query
        signal_id = int(query.data.split(":")[1])
        signal = self.signal_engine.get_signal(signal_id)

        if not signal or signal.status != SignalStatus.PENDING:
            await query.edit_message_text("⚠️ Signal no longer pending.")
            return

        # Execute trade
        order = Order(
            signal_id=signal.id,
            order_type="market",
            symbol=signal.symbol,
            action=signal.action,
            shares=1,  # Placeholder — real sizing from funding plan
            status="pending",
        )
        result = await self.broker.place_order(order)
        self.signal_engine.approve_signal(signal_id)

        await query.edit_message_text(f"✅ Approved — Order #{result.order_id}\n{result.message}")

    async def handle_reject(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reject a pending signal and record for what-if tracking."""
        query = update.callback_query
        signal_id = int(query.data.split(":")[1])
        signal = self.signal_engine.get_signal(signal_id)

        if not signal or signal.status != SignalStatus.PENDING:
            await query.edit_message_text("⚠️ Signal no longer pending.")
            return

        self.signal_engine.reject_signal(signal_id, price_at_pass=0)
        await query.edit_message_text("❌ Rejected")

    # --- Commands ---

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status — show NAV, positions count, and mode."""
        pv = self.db.fetchone("SELECT * FROM portfolio_value ORDER BY date DESC LIMIT 1")
        positions = self.db.fetchall("SELECT * FROM positions WHERE shares > 0")
        mode_label = "Mock" if self.mode == Mode.MOCK else "Live"
        ks = self.db.fetchone("SELECT active FROM kill_switch ORDER BY id DESC LIMIT 1")
        kill_active = ks["active"] if ks else False

        nav = pv["total_value"] if pv else 0
        cash = pv["cash"] if pv else 0
        count = len(positions) if positions else 0

        text = (
            f"📊 Portfolio Status\n\n"
            f"NAV: ${nav:,.0f}\n"
            f"Cash: ${cash:,.0f}\n"
            f"Positions: {count}\n"
            f"Mode: {mode_label}\n"
            f"Kill Switch: {'🔴 ON' if kill_active else '🟢 OFF'}"
        )
        await update.message.reply_text(text)

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /positions — show top positions."""
        rows = self.db.fetchall(
            "SELECT symbol, shares, avg_cost FROM positions"
            " WHERE shares > 0 ORDER BY shares DESC LIMIT 10"
        )
        if not rows:
            await update.message.reply_text("No open positions.")
            return

        lines = ["📈 Top Positions\n"]
        for r in rows:
            lines.append(f"  {r['symbol']}: {r['shares']} shares @ ${r['avg_cost']:.2f}")
        await update.message.reply_text("\n".join(lines))

    async def cmd_killswitch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /killswitch — toggle kill switch on/off."""
        ks = self.db.fetchone("SELECT id, active FROM kill_switch ORDER BY id DESC LIMIT 1")
        if not ks:
            await update.message.reply_text("⚠️ No kill switch configured.")
            return

        new_state = not ks["active"]
        self.db.execute("UPDATE kill_switch SET active = ? WHERE id = ?", (new_state, ks["id"]))
        self.db.connect().commit()

        label = "🔴 ON — All trading halted" if new_state else "🟢 OFF — Trading active"
        await update.message.reply_text(f"Kill Switch: {label}")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help — show available commands."""
        mode_label = "Mock" if self.mode == Mode.MOCK else "Live"
        text = (
            "💰 *Money Moves* — Investment Engine\n"
            f"Mode: {mode_label}\n\n"
            "*Commands:*\n"
            "/status — NAV, returns, exposure\n"
            "/positions — Open positions summary\n"
            "/killswitch — Toggle emergency trading halt\n"
            "/mode — Show current mode\n"
            "/help — This message\n\n"
            "Signal notifications appear here with "
            "Approve/Reject buttons when generated."
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /mode — show current execution mode."""
        mode_label = "Mock" if self.mode == Mode.MOCK else "Live"
        await update.message.reply_text(f"Mode: {mode_label}")

    # --- Signal expiry ---

    async def check_expired_signals(self) -> list[int]:
        """Mark pending signals older than 24h as ignored.

        Returns:
            List of expired signal IDs.
        """
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        rows = self.db.fetchall(
            "SELECT id FROM signals WHERE status = ? AND created_at < ?",
            (SignalStatus.PENDING.value, cutoff),
        )
        expired_ids: list[int] = []
        for row in rows:
            self.signal_engine.expire_signal(row["id"])
            expired_ids.append(row["id"])
        return expired_ids
