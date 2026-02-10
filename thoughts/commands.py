"""Telegram command handlers for the thoughts module.

Commands: /think, /think result handling, /note, /journal, /brief, /trade.
Called from the moves bot's telegram handler.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bridge import ThoughtsBridge
from context_builder import (
    build_context,
    compute_slow_to_act_gates,
)
from engine import ThoughtsEngine
from feedback import (
    apply_conviction_change,
    apply_research_to_db,
    format_research_summary,
    parse_think_output,
)
from spawner import build_task


def _get_engine() -> ThoughtsEngine:
    """Get or create the singleton engine."""
    return ThoughtsEngine()


def _get_bridge() -> ThoughtsBridge:
    """Get or create the singleton bridge."""
    return ThoughtsBridge(_get_engine())


def cmd_think(idea: str) -> dict[str, Any]:
    """Build a /think research session for an idea.

    Looks up existing thesis, builds context, and produces the
    task string for the sub-agent. The caller (Munny) relays
    the task to OpenClaw's sessions_spawn.

    Args:
        idea: Thesis name, ID, or new idea to research.

    Returns:
        Dict with keys:
            - message: Status message for Telegram
            - task: Full task string for sessions_spawn (or None)
            - thesis_id: Matched thesis ID (or None for new ideas)
            - is_new: Whether this is a new idea
    """
    engine = _get_engine()
    bridge = _get_bridge()

    ctx, formatted = build_context(engine, bridge, idea)
    thesis = ctx.get("thesis")
    gates = compute_slow_to_act_gates(ctx)
    task = build_task(idea, formatted, gates)

    if thesis:
        # Record a new session
        session_key = f"thoughts-thesis-{thesis['id']}"
        session_id = engine.create_session(thesis['id'], session_key)
        conviction = thesis.get("conviction", 0) or 0
        pct = int(conviction) if conviction > 1 else int(conviction * 100)

        message = (
            f"🧠 Deepening thesis: {thesis['title']}\n"
            f"Conviction: {pct}% | "
            f"Sessions: {gates['session_count']}\n"
            f"Session #{session_id} started.\n\n"
            f"Spawning research sub-agent..."
        )
        return {
            "message": message,
            "task": task,
            "thesis_id": thesis["id"],
            "is_new": False,
        }

    message = (
        f"🧠 New idea: {idea}\n"
        f"No existing thesis found — researching from scratch.\n\n"
        f"Spawning research sub-agent..."
    )
    return {
        "message": message,
        "task": task,
        "thesis_id": None,
        "is_new": True,
    }


def cmd_think_result(
    raw_output: str,
    thesis_id: int | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    """Process the raw output from a /think sub-agent session.

    Parses the JSON result, saves research artifacts, formats a
    summary for the user, and returns pending approvals with
    inline button callback data.

    Args:
        raw_output: Raw text from the sub-agent session.
        thesis_id: Thesis ID being researched (None for new ideas).
        session_id: Session ID to mark complete.

    Returns:
        Dict with keys:
            - message: Formatted summary for Telegram
            - applied: List of auto-applied changes
            - pending: List of pending approval dicts
            - buttons: List of inline button specs [{text, callback_data}]
            - parsed: True if output was successfully parsed
    """
    output = parse_think_output(raw_output)

    if output is None:
        return {
            "message": (
                "⚠️ Could not parse sub-agent output.\n"
                "The research session may not have produced valid JSON.\n\n"
                "Raw output (truncated):\n"
                f"```\n{raw_output[:500]}\n```"
            ),
            "applied": [],
            "pending": [],
            "buttons": [],
            "parsed": False,
        }

    engine = _get_engine()

    # Get thesis title for display
    thesis_title: str | None = None
    if thesis_id:
        thesis = engine.get_thesis(thesis_id)
        if thesis:
            thesis_title = thesis.get("title")

    # Format the user-facing summary
    message = format_research_summary(output, thesis_title)

    # Apply auto-changes and collect pending approvals
    applied: list[str] = []
    pending: list[dict[str, Any]] = []
    buttons: list[dict[str, str]] = []

    if thesis_id:
        result = apply_research_to_db(engine, thesis_id, output, session_id)
        applied = result["applied"]
        pending = result["pending"]

        # Build inline buttons for each pending approval
        for i, p in enumerate(pending):
            if p["type"] == "conviction_change":
                new_val = p["new_value"]
                buttons.append({
                    "text": f"✅ Update conviction → {int(new_val)}%",
                    "callback_data": (
                        f"think_approve:conviction:{thesis_id}:{new_val}"
                    ),
                })
                buttons.append({
                    "text": "❌ Keep current conviction",
                    "callback_data": f"think_reject:conviction:{thesis_id}",
                })
            elif p["type"] == "thesis_update":
                buttons.append({
                    "text": "✅ Apply thesis update",
                    "callback_data": (
                        f"think_approve:thesis:{thesis_id}"
                    ),
                })
                buttons.append({
                    "text": "❌ Skip thesis update",
                    "callback_data": f"think_reject:thesis:{thesis_id}",
                })

        if applied:
            message += "\n\n✅ **Auto-applied:**\n" + "\n".join(
                f"  • {a}" for a in applied
            )

        if pending:
            message += "\n\n⏳ **Awaiting your approval:**"
            for p in pending:
                if p["type"] == "conviction_change":
                    message += (
                        f"\n  • Conviction: {p.get('old_value', '?')}% → "
                        f"{int(p['new_value'])}%"
                    )
                elif p["type"] == "thesis_update":
                    parts = []
                    if p.get("title"):
                        parts.append(f"title → {p['title']}")
                    if p.get("status"):
                        parts.append(f"status → {p['status']}")
                    message += f"\n  • Thesis update: {', '.join(parts)}"
    else:
        message += (
            "\n\n💡 No thesis linked — research saved as standalone."
        )

    return {
        "message": message,
        "applied": applied,
        "pending": pending,
        "buttons": buttons,
        "parsed": True,
    }


def cmd_think_approve(callback_data: str) -> str:
    """Handle approval of a pending /think change.

    Args:
        callback_data: Callback string like "think_approve:conviction:3:75"
            or "think_approve:thesis:3".

    Returns:
        Confirmation message.
    """
    parts = callback_data.split(":")
    if len(parts) < 3:
        return "❌ Invalid approval data."

    action = parts[1]  # "conviction" or "thesis"
    thesis_id = int(parts[2])
    engine = _get_engine()

    if action == "conviction" and len(parts) >= 4:
        new_val = float(parts[3])
        success = apply_conviction_change(engine, thesis_id, new_val)
        if success:
            return f"✅ Conviction updated to {int(new_val)}% for thesis #{thesis_id}."
        return f"❌ Failed to update conviction for thesis #{thesis_id}."

    if action == "thesis":
        # For thesis updates, we'd need to stash the pending data
        # For now, acknowledge the approval
        return f"✅ Thesis #{thesis_id} update acknowledged. (Details already logged.)"

    return "❌ Unknown approval type."


def cmd_think_reject(callback_data: str) -> str:
    """Handle rejection of a pending /think change.

    Args:
        callback_data: Callback string like "think_reject:conviction:3".

    Returns:
        Confirmation message.
    """
    parts = callback_data.split(":")
    if len(parts) < 3:
        return "❌ Invalid rejection data."

    action = parts[1]
    thesis_id = int(parts[2])

    if action == "conviction":
        return f"⏭️ Conviction change for thesis #{thesis_id} skipped."
    if action == "thesis":
        return f"⏭️ Thesis update for #{thesis_id} skipped."

    return "❌ Unknown rejection type."


def cmd_note(text: str) -> str:
    """Capture a quick observation, auto-tagged to relevant thesis.

    Scans active theses for symbol/keyword matches and links
    the note accordingly.

    Args:
        text: The observation text.

    Returns:
        Confirmation message for Telegram.
    """
    engine = _get_engine()
    text_upper = text.upper()

    # Try to auto-link to a thesis by matching symbols
    linked_thesis_id: int | None = None
    linked_symbol: str | None = None

    for thesis in engine.get_theses():
        symbols = _parse_thesis_symbols(thesis)
        for sym in symbols:
            if sym in text_upper:
                linked_thesis_id = thesis["id"]
                linked_symbol = sym
                break
        if linked_thesis_id:
            break

    # Also try matching thesis title keywords
    if not linked_thesis_id:
        text_lower = text.lower()
        for thesis in engine.get_theses():
            title_words = thesis["title"].lower().split()
            # Match if any significant word (>3 chars) appears
            for word in title_words:
                if len(word) > 3 and word in text_lower:
                    linked_thesis_id = thesis["id"]
                    break
            if linked_thesis_id:
                break

    thought_id = engine.add_thought(
        content=text,
        linked_thesis_id=linked_thesis_id,
        linked_symbol=linked_symbol,
    )

    tag_info = ""
    if linked_thesis_id:
        tag_info = f" → linked to thesis #{linked_thesis_id}"
        if linked_symbol:
            tag_info += f" ({linked_symbol})"

    return f"📝 Note #{thought_id} captured{tag_info}."


def cmd_journal() -> str:
    """Read-only view of recent sessions, notes, and thesis history.

    Returns:
        Formatted journal listing for Telegram.
    """
    engine = _get_engine()

    sections: list[str] = ["📓 **Journal**\n"]

    # Active theses summary
    theses = engine.get_theses()
    if theses:
        sections.append("**Active Theses:**")
        for t in theses[:5]:
            conviction = t.get("conviction", 0) or 0
            pct = int(conviction) if conviction > 1 else int(conviction * 100)
            sections.append(
                f"  • {t['title']} — {pct}% conviction"
            )
        sections.append("")

    # Recent sessions
    completed = engine.list_sessions(status="completed")
    active = engine.list_sessions(status="active")
    all_sessions = active + completed
    if all_sessions:
        sections.append("**Recent Sessions:**")
        for s in all_sessions[:5]:
            date = s.get("created_at", "")[:10]
            status = s.get("status", "?")
            summary = (s.get("summary") or "No summary")[:80]
            sections.append(
                f"  • [{date}] #{s['id']} ({status}): {summary}"
            )
        sections.append("")

    # Recent notes
    thoughts = engine.list_thoughts(limit=5)
    if thoughts:
        sections.append("**Recent Notes:**")
        for t in thoughts:
            date = t.get("created_at", "")[:10]
            content = t["content"][:80]
            tag = ""
            if t.get("linked_symbol"):
                tag = f" [{t['linked_symbol']}]"
            sections.append(f"  • [{date}]{tag} {content}")
        sections.append("")

    # Recent journals
    journals = engine.list_journals(limit=5)
    if journals:
        sections.append("**Recent Journals:**")
        for j in journals:
            date = j.get("created_at", "")[:10]
            emoji = {
                "research": "🔬", "review": "📊",
                "discovery": "🔍", "thought": "💭",
            }.get(j["journal_type"], "📝")
            sections.append(
                f"  {emoji} [{date}] {j['title'][:60]}"
            )

    if len(sections) == 1:
        return "📓 No journal entries yet. Use /think to start."

    return "\n".join(sections)


def cmd_brief() -> str:
    """Daily briefing: live prices vs triggers, thesis status, upcoming earnings.

    Returns:
        Formatted briefing for Telegram.
    """
    engine = _get_engine()
    sections: list[str] = ["📊 **Daily Brief**\n"]

    # Gather data
    all_symbols, thesis_symbols, triggers, prices = _gather_brief_data(engine)

    # Generate sections
    _add_thesis_section(sections, engine.get_theses(), thesis_symbols, prices)
    _add_trigger_proximity_section(sections, triggers, prices)
    _add_earnings_section(sections, all_symbols)
    _add_recent_notes_section(sections, engine)
    _add_pending_signals_section(sections, engine)

    if len(sections) == 1:
        return "📊 Nothing to brief on yet. Add theses with /think."

    return "\n".join(sections)


def _gather_brief_data(engine):
    """Gather all data needed for the brief."""
    # Gather all symbols from theses + watchlist triggers
    theses = engine.get_theses()
    all_symbols: set[str] = set()
    thesis_symbols: dict[int, list[str]] = {}

    for t in theses:
        syms = _parse_thesis_symbols(t)
        thesis_symbols[t["id"]] = syms
        all_symbols.update(syms)

    # Get watchlist triggers from moves DB
    triggers = engine._moves_query(
        "SELECT * FROM watchlist_triggers WHERE active = 1 ORDER BY symbol"
    )
    for tr in triggers:
        all_symbols.add(tr["symbol"].upper())

    # Fetch live prices
    prices: dict[str, float] = {}
    if all_symbols:
        prices = _fetch_prices(sorted(all_symbols))

    return all_symbols, thesis_symbols, triggers, prices


def _add_thesis_section(sections: list[str], theses, thesis_symbols, prices):
    """Add thesis summary with live prices to sections."""
    if not theses:
        return

    sections.append("**Theses:**")
    for t in theses:
        conviction = t.get("conviction", 0) or 0
        pct = int(conviction) if conviction > 1 else int(conviction * 100)
        syms = thesis_symbols.get(t["id"], [])
        sym_prices = []
        for s in syms:
            p = prices.get(s)
            if p:
                sym_prices.append(f"{s} ${p:.2f}")
            else:
                sym_prices.append(s)
        sym_str = ", ".join(sym_prices) if sym_prices else "no symbols"
        sections.append(f"  • {t['title']} ({pct}%) — {sym_str}")
    sections.append("")


def _add_trigger_proximity_section(sections: list[str], triggers, prices):
    """Add watchlist trigger proximity analysis to sections."""
    if not (triggers and prices):
        return

    sections.append("**Trigger Proximity:**")
    # Group by symbol
    by_symbol: dict[str, list[dict]] = {}
    for tr in triggers:
        sym = tr["symbol"].upper()
        by_symbol.setdefault(sym, []).append(tr)

    for sym in sorted(by_symbol):
        current = prices.get(sym)
        if not current:
            continue
        for tr in by_symbol[sym]:
            target = tr["target_value"]
            pct_away = ((target - current) / current) * 100
            direction = "↑" if pct_away > 0 else "↓"
            ttype = tr["trigger_type"].replace("_", " ")
            alert = ""
            if abs(pct_away) < 5:
                alert = " ⚠️ CLOSE"
            elif abs(pct_away) < 10:
                alert = " 👀"
            sections.append(
                f"  • {sym} {ttype}: ${target:.0f} "
                f"({direction}{abs(pct_away):.1f}% from ${current:.2f}){alert}"
            )
    sections.append("")


def _add_earnings_section(sections: list[str], all_symbols):
    """Add upcoming earnings information to sections."""
    try:
        from datetime import date as date_type
        from datetime import timedelta

        today = date_type.today()
        week_out = today + timedelta(days=7)
        earnings_items: list[str] = []

        for sym in sorted(all_symbols):
            earnings_date = _get_earnings_date(sym)
            if earnings_date and today <= earnings_date <= week_out:
                days = (earnings_date - today).days
                urgency = " ⏰" if days <= 2 else ""
                earnings_items.append(
                    f"  • {sym}: {earnings_date.isoformat()} "
                    f"({days}d away){urgency}"
                )

        if earnings_items:
            sections.append("**Earnings This Week:**")
            sections.extend(earnings_items)
            sections.append("")
    except Exception:
        pass


def _get_earnings_date(symbol: str):
    """Get earnings date for a symbol."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or (hasattr(cal, "empty") and cal.empty):
            return None

        # cal can be a dict or DataFrame
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                ed = ed[0]
            if ed and hasattr(ed, "date"):
                return ed.date()
        else:
            # DataFrame
            if "Earnings Date" in cal.columns:
                ed = cal["Earnings Date"].iloc[0]
                if hasattr(ed, "date"):
                    return ed.date()
        return None
    except Exception:
        return None


def _add_recent_notes_section(sections: list[str], engine):
    """Add recent notes to sections."""
    thoughts = engine.list_thoughts(limit=3)
    if not thoughts:
        return

    sections.append("**Recent Notes:**")
    for t in thoughts:
        date = t.get("created_at", "")[:10]
        content = t["content"][:60]
        tag = f" [{t['linked_symbol']}]" if t.get("linked_symbol") else ""
        sections.append(f"  • [{date}]{tag} {content}")
    sections.append("")


def _add_pending_signals_section(sections: list[str], engine):
    """Add pending signals to sections."""
    pending = engine.get_signals(status="pending")
    if not pending:
        return

    sections.append(f"**Pending Signals:** {len(pending)}")
    for sig in pending[:3]:
        sections.append(
            f"  • {sig.get('direction', '?')} {sig.get('symbol', '?')} "
            f"— {sig.get('reasoning', 'no reason')[:60]}"
        )
    sections.append("")


def _fetch_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch current prices for a list of symbols using yfinance.

    Args:
        symbols: List of ticker symbols.

    Returns:
        Dict mapping symbol to current price.
    """
    prices: dict[str, float] = {}
    try:
        import yfinance as yf

        for sym in symbols:
            try:
                ticker = yf.Ticker(sym)
                info = ticker.fast_info
                price = getattr(info, "last_price", None)
                if price and price > 0:
                    prices[sym] = float(price)
            except Exception:
                continue
    except ImportError:
        pass
    return prices


def _parse_thesis_symbols(thesis: dict[str, Any]) -> list[str]:
    """Extract symbol list from a thesis dict."""
    raw = thesis.get("symbols", "[]")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return list(parsed) if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            # Comma-separated string
            return [s.strip() for s in raw.split(",") if s.strip()]
    return list(raw) if raw else []


# ── /trade command ───────────────────────────────────────────────


_TRADE_RE = re.compile(
    r"(?i)^(buy|sell)\s+"
    r"([A-Z]{1,10})\s+"
    r"(\d+(?:\.\d+)?)\s*"
    r"@\s*\$?(\d+(?:\.\d+)?)$"
)


def parse_trade_command(text: str) -> dict[str, Any] | None:
    """Parse '/trade BUY META 10 @ 650.00' into a dict.

    Returns None if the format doesn't match.
    """
    text = text.strip()
    m = _TRADE_RE.match(text)
    if not m:
        return None
    return {
        "action": m.group(1).upper(),
        "symbol": m.group(2).upper(),
        "shares": float(m.group(3)),
        "price": float(m.group(4)),
    }


def cmd_trade(text: str) -> dict[str, Any]:
    """Handle /trade command — log a manual trade.

    Usage:
        /trade BUY META 10 @ 650.00
        /trade SELL QCOM 50 @ 140.00

    Calls the moves API to record the trade and update positions.

    Args:
        text: Everything after '/trade '.

    Returns:
        Dict with 'message' key for Telegram response.
    """
    parsed = parse_trade_command(text)
    if not parsed:
        return {
            "message": (
                "❌ Invalid format. Use:\n"
                "`/trade BUY META 10 @ 650.00`\n"
                "`/trade SELL QCOM 50 @ 140.00`"
            )
        }

    # Try API first, fall back to direct DB
    try:
        import requests

        resp = requests.post(
            "http://localhost:8000/api/fund/trades/manual",
            json=parsed,
            timeout=10,
        )
        if resp.ok:
            d = resp.json()
            return {"message": f"✅ {d['message']}"}
        else:
            detail = resp.json().get("detail", resp.text)
            return {"message": f"❌ {detail}"}
    except Exception:
        pass

    # Direct DB fallback
    try:
        import sys
        from pathlib import Path

        moves_root = Path(__file__).resolve().parent.parent / "moves"
        if str(moves_root) not in sys.path:
            sys.path.insert(0, str(moves_root))

        from db.database import Database

        db_path = moves_root / "data" / "moves_live.db"
        if not db_path.exists():
            db_path = moves_root / "data" / "moves_mock.db"
        db = Database(db_path)

        symbol = parsed["symbol"]
        action = parsed["action"]
        shares = parsed["shares"]
        price = parsed["price"]
        total = shares * price

        if action == "SELL":
            pos = db.fetchone(
                "SELECT shares FROM positions WHERE symbol = ?",
                (symbol,),
            )
            held = pos["shares"] if pos else 0
            if held < shares:
                return {
                    "message": (
                        f"❌ Can't sell {shares} {symbol} "
                        f"— only hold {held}"
                    )
                }

        if action == "BUY":
            pos = db.fetchone(
                "SELECT id, shares, avg_cost FROM positions "
                "WHERE symbol = ?",
                (symbol,),
            )
            if pos and pos["shares"] > 0:
                new_shares = pos["shares"] + shares
                new_avg = (
                    (pos["shares"] * pos["avg_cost"] + shares * price)
                    / new_shares
                )
                db.execute(
                    "UPDATE positions SET shares=?, avg_cost=?, "
                    "updated_at=datetime('now') WHERE id=?",
                    (new_shares, new_avg, pos["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO positions (symbol, shares, avg_cost, side)"
                    " VALUES (?, ?, ?, 'long')",
                    (symbol, shares, price),
                )
        else:
            db.execute(
                "UPDATE positions SET shares = shares - ?, "
                "updated_at=datetime('now') WHERE symbol = ?",
                (shares, symbol),
            )

        db.execute(
            "INSERT INTO trades (symbol, action, shares, price, "
            "total_value, broker, timestamp) "
            "VALUES (?, ?, ?, ?, ?, 'manual', datetime('now'))",
            (symbol, action, shares, price, total),
        )
        db.connect().commit()

        return {
            "message": (
                f"✅ {action} {shares} {symbol} @ ${price:,.2f} "
                f"(${total:,.2f}) logged"
            )
        }
    except Exception as e:
        return {"message": f"❌ Failed to log trade: {e}"}
