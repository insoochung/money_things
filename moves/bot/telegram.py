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


def format_signal_message(signal: Signal, db: Database | None = None) -> str:
    """Build the Telegram notification text for a new signal.

    Includes thesis context, price info, position sizing, and principles
    that influenced the confidence score.

    Args:
        signal: Signal to format.
        db: Optional database for enriching with thesis/position context.

    Returns:
        Formatted message string with signal details.
    """
    pct = round(signal.confidence * 100)
    label = _confidence_label(signal.confidence)
    action_emoji = "🟢" if signal.action.value in ("BUY", "COVER") else "🔴"

    lines = [
        f"{action_emoji} {signal.action.value} {signal.symbol}",
        f"{'━' * 28}",
        "",
        f"📊 Confidence: {pct}% ({label})",
        f"📡 Source: {signal.source}",
    ]

    # Add thesis context if available
    if db and signal.thesis_id:
        thesis = db.fetchone(
            "SELECT title, status, horizon, conviction FROM theses WHERE id = ?",
            (signal.thesis_id,),
        )
        if thesis:
            lines += [
                "",
                f"📋 Thesis: {thesis['title']}",
                f"   Status: {thesis['status'].upper()} | "
                f"Conviction: {round((thesis['conviction'] or 0) * 100)}%",
            ]
            if thesis["horizon"]:
                lines.append(f"   Horizon: {thesis['horizon']}")

    # Reasoning
    if signal.reasoning:
        lines += ["", f"💡 {signal.reasoning}"]

    # Position sizing
    if signal.size_pct:
        lines += ["", f"📐 Size: {signal.size_pct}% of NAV"]

    # Funding plan
    if signal.funding_plan:
        lines += [f"💰 Funding: {signal.funding_plan}"]

    # Current position context
    if db:
        pos = db.fetchone(
            "SELECT shares, avg_cost, side FROM positions WHERE symbol = ? AND shares > 0",
            (signal.symbol,),
        )
        if pos:
            lines += [
                "",
                f"📦 Current: {pos['shares']:.0f} shares {pos['side']} "
                f"@ ${pos['avg_cost']:.2f}",
            ]
        else:
            lines += ["", "📦 New position (not currently held)"]

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
        self.app.add_handler(CommandHandler("think", self.cmd_think))
        self.app.add_handler(CommandHandler("note", self.cmd_note))
        self.app.add_handler(CommandHandler("journal", self.cmd_journal))
        self.app.add_handler(CommandHandler("brief", self.cmd_brief))
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

        text = format_signal_message(signal, db=self.db)
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
            "💰 *Munny Thoughts* — Investment Engine\n"
            f"Mode: {mode_label}\n\n"
            "*Journal:*\n"
            "/think <idea> — Research & develop thesis\n"
            "/note <text> — Quick observation\n"
            "/journal — Recent sessions & theses\n"
            "/brief — Daily briefing with live prices\n\n"
            "*Portfolio:*\n"
            "/status — NAV, returns, exposure\n"
            "/positions — Open positions\n"
            "/killswitch — Emergency trading halt\n"
            "/mode — Current mode\n"
            "/help — This message\n\n"
            "Signals appear with Approve/Reject buttons."
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /mode — show current execution mode."""
        mode_label = "Mock" if self.mode == Mode.MOCK else "Live"
        await update.message.reply_text(f"Mode: {mode_label}")

    # --- Thoughts integration (3 commands + brief) ---

    def _get_thoughts_commands(self) -> tuple:
        """Import and return the thoughts commands module."""
        import sys
        thoughts_path = "/root/workspace/money/thoughts"
        if thoughts_path not in sys.path:
            sys.path.insert(0, thoughts_path)
        from commands import cmd_brief, cmd_journal, cmd_note, cmd_think
        return cmd_think, cmd_note, cmd_journal, cmd_brief

    async def cmd_think(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /think <idea> — research and develop a thesis.

        Returns the status message immediately, then the task string
        is available for Munny to spawn a sub-agent.
        """
        args = " ".join(context.args) if context.args else ""
        if not args:
            await update.message.reply_text(
                "Usage: /think <thesis name, ID, or new idea>"
            )
            return
        try:
            cmd_think_fn, _, _, _ = self._get_thoughts_commands()
            result = cmd_think_fn(args)
            await update.message.reply_text(result["message"])
            # Store task for Munny to pick up via heartbeat/cron
            if result.get("task"):
                logger.info(
                    "Think task ready for thesis_id=%s (new=%s)",
                    result.get("thesis_id"), result.get("is_new"),
                )
        except Exception as e:
            logger.exception("Think error: %s", e)
            await update.message.reply_text(f"⚠️ Error: {e}")

    async def cmd_note(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /note <text> — quick observation, auto-tagged."""
        text = " ".join(context.args) if context.args else ""
        if not text:
            await update.message.reply_text("Usage: /note <your observation>")
            return
        try:
            _, cmd_note_fn, _, _ = self._get_thoughts_commands()
            result = cmd_note_fn(text)
            await update.message.reply_text(result)
        except Exception as e:
            logger.exception("Note error: %s", e)
            await update.message.reply_text(f"⚠️ Error: {e}")

    async def cmd_journal(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /journal — read-only view of theses, sessions, notes."""
        try:
            _, _, cmd_journal_fn, _ = self._get_thoughts_commands()
            result = cmd_journal_fn()
            await update.message.reply_text(result)
        except Exception as e:
            logger.exception("Journal error: %s", e)
            await update.message.reply_text(f"⚠️ Error: {e}")

    async def cmd_brief(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /brief — daily briefing with live prices."""
        try:
            await update.message.reply_text("📊 Fetching live data...")
            _, _, _, cmd_brief_fn = self._get_thoughts_commands()
            result = cmd_brief_fn()
            # Split if too long for Telegram (4096 char limit)
            if len(result) > 4000:
                for i in range(0, len(result), 4000):
                    await update.message.reply_text(result[i:i + 4000])
            else:
                await update.message.reply_text(result)
        except Exception as e:
            logger.exception("Brief error: %s", e)
            await update.message.reply_text(f"⚠️ Error: {e}")

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
