"""Build context packets for /think research sessions.

Given a thesis idea or title, gathers all relevant data from both
the thoughts DB and moves DB to provide the sub-agent with full context.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from bridge import ThoughtsBridge
from engine import ThoughtsEngine


def find_thesis_by_idea(
    engine: ThoughtsEngine, idea: str
) -> dict[str, Any] | None:
    """Find an existing thesis matching the idea string.

    Searches by ID (if numeric) or fuzzy title match.

    Args:
        engine: ThoughtsEngine instance.
        idea: Thesis ID (numeric string) or title substring.

    Returns:
        Thesis dict from moves DB, or None if not found.
    """
    try:
        thesis_id = int(idea)
        thesis = engine.get_thesis(thesis_id)
        if thesis:
            return thesis
    except ValueError:
        pass

    idea_lower = idea.lower()
    for t in engine.get_theses():
        if idea_lower in t["title"].lower():
            return t
    return None


def _parse_symbols(thesis: dict[str, Any]) -> list[str]:
    """Extract symbol list from a thesis dict."""
    raw = thesis.get("symbols", "")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return list(parsed) if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            return [s.strip() for s in raw.split(",") if s.strip()]
    return list(raw) if raw else []


def gather_thesis_context(
    engine: ThoughtsEngine,
    bridge: ThoughtsBridge,
    thesis: dict[str, Any],
) -> dict[str, Any]:
    """Gather full context for an existing thesis.

    Pulls thesis details, positions, notes, past sessions,
    signals, and recent trades from both DBs.

    Args:
        engine: ThoughtsEngine instance.
        bridge: ThoughtsBridge instance.
        thesis: Thesis dict from moves DB.

    Returns:
        Structured context dict with all relevant data.
    """
    thesis_id = thesis["id"]
    symbols = _parse_symbols(thesis)

    # Past research sessions for this thesis
    all_sessions = engine.list_sessions(status="completed")
    thesis_sessions = [
        s for s in all_sessions if s.get("thesis_id") == thesis_id
    ]

    # Recent notes/thoughts linked to this thesis
    all_thoughts = engine.list_thoughts(limit=50)
    linked_thoughts = [
        t for t in all_thoughts
        if t.get("linked_thesis_id") == thesis_id
        or (t.get("linked_symbol") and t["linked_symbol"] in symbols)
    ]

    # Research notes per symbol
    research_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for sym in symbols:
        notes = engine.get_research(sym)
        if notes:
            research_by_symbol[sym] = notes[:3]  # last 3 per symbol

    # Positions for thesis symbols
    all_positions = engine.get_positions()
    positions = [p for p in all_positions if p.get("symbol") in symbols]

    # Signals for this thesis
    signals = engine.get_signals(thesis_id=thesis_id)

    # Journals for this thesis
    journals = engine.list_journals(thesis_id=thesis_id, limit=5)

    return {
        "thesis": thesis,
        "symbols": symbols,
        "positions": positions,
        "signals": signals,
        "research_by_symbol": research_by_symbol,
        "past_sessions": thesis_sessions,
        "linked_thoughts": linked_thoughts[:10],
        "journals": journals,
    }


def build_new_idea_context(idea: str) -> dict[str, Any]:
    """Build context for a brand-new idea with no existing thesis.

    Args:
        idea: The raw idea string from the user.

    Returns:
        Minimal context dict for the sub-agent.
    """
    return {
        "thesis": None,
        "idea": idea,
        "symbols": [],
        "positions": [],
        "signals": [],
        "research_by_symbol": {},
        "past_sessions": [],
        "linked_thoughts": [],
        "journals": [],
    }


def _format_thesis_section(thesis: dict[str, Any]) -> str:
    """Format thesis details as readable text."""
    conviction = thesis.get("conviction", 0)
    pct = int(conviction) if conviction and conviction > 1 else int((conviction or 0) * 100)
    return (
        f"**Thesis #{thesis['id']}: {thesis['title']}**\n"
        f"Status: {thesis.get('status', 'unknown')} | "
        f"Conviction: {pct}%\n"
        f"Updated: {thesis.get('updated_at', 'unknown')}"
    )


def _format_positions_section(positions: list[dict[str, Any]]) -> str:
    """Format positions as readable text."""
    if not positions:
        return "No open positions for this thesis."
    lines = []
    for p in positions:
        lines.append(
            f"- {p['symbol']}: {p.get('shares', 0)} shares "
            f"@ ${p.get('avg_cost', 0):.2f}"
        )
    return "\n".join(lines)


def _format_thoughts_section(
    thoughts: list[dict[str, Any]],
) -> str:
    """Format linked thoughts/notes as readable text."""
    if not thoughts:
        return "No recent notes."
    lines = []
    for t in thoughts[:5]:
        date = t.get("created_at", "")[:10]
        lines.append(f"- [{date}] {t['content'][:120]}")
    return "\n".join(lines)


def _format_sessions_section(
    sessions: list[dict[str, Any]],
) -> str:
    """Format past sessions as readable text."""
    if not sessions:
        return "No prior research sessions."
    lines = []
    for s in sessions[:5]:
        summary = s.get("summary", "No summary")
        date = s.get("created_at", "")[:10]
        lines.append(f"- [{date}] Session #{s['id']}: {summary[:100]}")
    return "\n".join(lines)


def compute_slow_to_act_gates(
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compute slow-to-act gate status for the thesis.

    Returns a dict with gate checks:
    - session_count: how many completed sessions exist
    - meets_session_minimum: has >= 2 sessions
    - meets_conviction_threshold: conviction >= 0.7
    - cooldown_ok: thesis created > 1 week ago

    Args:
        context: The context dict from gather_thesis_context.

    Returns:
        Dict with gate statuses.
    """
    thesis = context.get("thesis")
    sessions = context.get("past_sessions", [])
    session_count = len(sessions)

    conviction = 0.0
    created_at_str = None
    if thesis:
        conviction = thesis.get("conviction", 0) or 0
        created_at_str = thesis.get("created_at")

    cooldown_ok = False
    if created_at_str:
        try:
            created = datetime.fromisoformat(created_at_str)
            cooldown_ok = (datetime.now() - created) > timedelta(weeks=1)
        except (ValueError, TypeError):
            pass

    return {
        "session_count": session_count,
        "meets_session_minimum": session_count >= 2,
        "meets_conviction_threshold": conviction >= 70 if conviction > 1 else conviction >= 0.7,
        "cooldown_ok": cooldown_ok,
        "can_generate_signals": (
            session_count >= 2
            and (conviction >= 70 if conviction > 1 else conviction >= 0.7)
            and cooldown_ok
        ),
    }


def format_context_packet(context: dict[str, Any]) -> str:
    """Format a context dict into a readable string for the sub-agent prompt.

    Args:
        context: Context dict from gather_thesis_context or
            build_new_idea_context.

    Returns:
        Formatted multi-line string with all context sections.
    """
    sections: list[str] = []

    thesis = context.get("thesis")
    if thesis:
        sections.append("## Existing Thesis")
        sections.append(_format_thesis_section(thesis))

        gates = compute_slow_to_act_gates(context)
        sections.append("\n## Slow-to-Act Gates")
        sections.append(
            f"- Sessions completed: {gates['session_count']} "
            f"(need ≥2: {'✅' if gates['meets_session_minimum'] else '❌'})"
        )
        sections.append(
            f"- Conviction threshold: "
            f"{'✅' if gates['meets_conviction_threshold'] else '❌'}"
        )
        sections.append(
            f"- Cooldown (1 week): "
            f"{'✅' if gates['cooldown_ok'] else '❌'}"
        )
        sections.append(
            f"- Can generate signals: "
            f"{'✅' if gates['can_generate_signals'] else '❌'}"
        )
    else:
        idea = context.get("idea", "Unknown")
        sections.append("## New Idea (No Existing Thesis)")
        sections.append(f"Idea: {idea}")
        sections.append(
            "This is a fresh idea. Research from scratch, "
            "discover relevant tickers, and draft a thesis."
        )

    symbols = context.get("symbols", [])
    if symbols:
        sections.append(f"\n## Symbols: {', '.join(symbols)}")

    positions = context.get("positions", [])
    sections.append("\n## Positions")
    sections.append(_format_positions_section(positions))

    research = context.get("research_by_symbol", {})
    if research:
        sections.append("\n## Prior Research")
        for sym, notes in research.items():
            latest = notes[0]
            sections.append(
                f"### {sym} (latest: {latest.get('title', 'untitled')})"
            )
            content_preview = latest.get("content", "")[:300]
            sections.append(content_preview)

    thoughts = context.get("linked_thoughts", [])
    sections.append("\n## Recent Notes")
    sections.append(_format_thoughts_section(thoughts))

    past = context.get("past_sessions", [])
    sections.append("\n## Past Sessions")
    sections.append(_format_sessions_section(past))

    return "\n".join(sections)


def build_context(
    engine: ThoughtsEngine,
    bridge: ThoughtsBridge,
    idea: str,
) -> tuple[dict[str, Any], str]:
    """Main entry point: build context for a /think session.

    Looks up whether the idea matches an existing thesis.
    If yes, gathers full context. If no, builds a new-idea context.

    Args:
        engine: ThoughtsEngine instance.
        bridge: ThoughtsBridge instance.
        idea: The user's idea or thesis reference.

    Returns:
        Tuple of (raw context dict, formatted context string).
    """
    thesis = find_thesis_by_idea(engine, idea)
    if thesis:
        ctx = gather_thesis_context(engine, bridge, thesis)
    else:
        ctx = build_new_idea_context(idea)

    formatted = format_context_packet(ctx)
    return ctx, formatted
