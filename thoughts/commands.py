"""Telegram command handlers for the thoughts module.

These functions are called from the moves bot's telegram handler
to dispatch thoughts-related commands.
"""

from __future__ import annotations

from bridge import ThoughtsBridge
from engine import ThoughtsEngine


def _get_engine() -> ThoughtsEngine:
    """Get or create the singleton engine."""
    return ThoughtsEngine()


def _get_bridge() -> ThoughtsBridge:
    """Get or create the singleton bridge."""
    return ThoughtsBridge(_get_engine())


def cmd_think(thesis_name: str) -> str:
    """Spawn or resume a thoughts session for a thesis.

    Args:
        thesis_name: Thesis name or ID to research.

    Returns:
        Status message for Telegram.
    """
    engine = _get_engine()

    # Try to find thesis by ID or name
    thesis_id: int | None = None
    thesis_title = thesis_name

    # Check if it's a numeric ID
    try:
        thesis_id = int(thesis_name)
        thesis = engine.get_thesis(thesis_id)
        if thesis:
            thesis_title = thesis["title"]
    except ValueError:
        # Search by name in moves DB theses
        for t in engine.get_theses():
            if thesis_name.lower() in t["title"].lower():
                thesis_id = t["id"]
                thesis_title = t["title"]
                break

    if thesis_id is None:
        return f"❓ No thesis found matching '{thesis_name}'. Create one in moves first."

    # Check for existing active session
    session = engine.get_active_session(thesis_id)
    if session:
        return (
            f"🧠 Resuming thoughts on: {thesis_title}\n"
            f"Session #{session['id']} (started {session['created_at']})\n\n"
            f"Use the sub-agent session to continue research."
        )

    # Create new session
    session_key = f"thoughts-thesis-{thesis_id}"
    session_id = engine.create_session(thesis_id, session_key)
    return (
        f"🧠 Starting research session on: {thesis_title}\n"
        f"Session #{session_id}\n\n"
        f"Spawning research sub-agent..."
    )


def cmd_journal(thesis_id: int | None = None) -> str:
    """List active thought threads / journals.

    Args:
        thesis_id: Optional thesis ID to filter by.

    Returns:
        Formatted journal listing for Telegram.
    """
    engine = _get_engine()
    journals = engine.list_journals(thesis_id=thesis_id, limit=10)

    if not journals:
        return "📓 No journal entries yet."

    lines = ["📓 Recent Journals\n"]
    for j in journals:
        type_emoji = {
            "research": "🔬",
            "review": "📊",
            "discovery": "🔍",
            "thought": "💭",
        }.get(j["journal_type"], "📝")
        thesis_tag = f" [T#{j['thesis_id']}]" if j["thesis_id"] else ""
        lines.append(f"{type_emoji} #{j['id']}: {j['title']}{thesis_tag}")
        lines.append(f"   {j['created_at'][:10]} • {j['journal_type']}")
    return "\n".join(lines)


def cmd_review(symbol: str) -> str:
    """Get current research take on a symbol.

    Args:
        symbol: Stock ticker symbol.

    Returns:
        Research summary for Telegram.
    """
    engine = _get_engine()
    note = engine.get_latest_research(symbol.upper())

    if not note:
        return f"📭 No research notes for {symbol.upper()}. Use /research to start."

    lines = [
        f"🔬 Research: {symbol.upper()}",
        f"{'━' * 28}",
        f"📋 {note['title']}",
    ]

    if note.get("confidence"):
        lines.append(f"📊 Confidence: {int(note['confidence'] * 100)}%")
    if note.get("fair_value_estimate"):
        lines.append(f"💰 Fair Value: ${note['fair_value_estimate']:.2f}")
    if note.get("bull_case"):
        lines.append(f"\n🐂 Bull: {note['bull_case'][:200]}")
    if note.get("bear_case"):
        lines.append(f"\n🐻 Bear: {note['bear_case'][:200]}")

    lines.append(f"\nUpdated: {note['updated_at'][:10]}")
    return "\n".join(lines)


def cmd_thought(text: str) -> str:
    """Capture a quick thought.

    Args:
        text: The thought to capture.

    Returns:
        Confirmation message.
    """
    engine = _get_engine()
    thought_id = engine.add_thought(content=text)
    return f"💭 Thought #{thought_id} captured."


def cmd_onboard(answers: dict[str, str] | None = None) -> str:
    """Run onboarding to generate an investor profile.

    Args:
        answers: Optional dict of interview answers keyed by question ID.
            If None, returns the interview questions formatted for Telegram.

    Returns:
        Interview questions or profile summary.
    """
    from onboard import OnboardingEngine

    engine = _get_engine()
    bridge = _get_bridge()
    onboard = OnboardingEngine(engine, bridge)

    if answers is None:
        questions = onboard.get_interview_questions()
        lines = ["📋 *Investor Onboarding*\n"]
        lines.append("Reply with `/onboard` followed by numbered answers:\n")
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q['question']}")
        lines.append("\nExample:")
        lines.append("`/onboard 1. Growth 2. Months 3. Tech 4. Moderate ...`")
        return "\n".join(lines)

    profile = onboard.get_combined_profile(answers)
    onboard.save_profile(profile)
    return f"✅ Investor profile saved!\n\n{profile[:1500]}"


def cmd_research(symbol: str) -> str:
    """Trigger a deep-dive research session on a symbol.

    Args:
        symbol: Stock ticker to research.

    Returns:
        Status message indicating research session is starting.
    """
    engine = _get_engine()
    symbol = symbol.upper()

    # Check if symbol is in any active thesis
    thesis_context = ""
    for thesis in engine.get_theses():
        symbols_str = thesis.get("symbols", "[]")
        try:
            import json

            symbols = json.loads(symbols_str) if symbols_str else []
        except (json.JSONDecodeError, TypeError):
            symbols = []
        if symbol in symbols:
            thesis_context = f"\nLinked to thesis: {thesis['title']} (#{thesis['id']})"
            break

    return (
        f"🔬 Starting deep-dive on {symbol}{thesis_context}\n\n"
        f"Spawning research sub-agent..."
    )
