"""Telegram command handlers for the thoughts module.

Three commands only: /think, /note, /journal.
Called from the moves bot's telegram handler.
"""

from __future__ import annotations

import json
from typing import Any

from bridge import ThoughtsBridge
from context_builder import (
    build_context,
    compute_slow_to_act_gates,
)
from engine import ThoughtsEngine
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
