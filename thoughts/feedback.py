"""Parse sub-agent /think output and apply thesis updates.

Takes the JSON output from a research sub-agent session,
validates it, and applies changes to the moves DB (with
user approval for conviction changes).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from engine import ThoughtsEngine
from spawner import ThinkOutput


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract JSON block from sub-agent output text.

    Looks for ```json ... ``` fenced blocks first, then tries
    to find raw JSON objects.

    Args:
        text: Raw sub-agent output text.

    Returns:
        Parsed dict or None if no valid JSON found.
    """
    # Try fenced JSON blocks
    pattern = r"```json\s*\n(.*?)\n\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue

    # Try to find a raw JSON object (last one, likely the output)
    brace_depth = 0
    start = None
    candidates: list[str] = []
    for i, ch in enumerate(text):
        if ch == "{":
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None

    for candidate in reversed(candidates):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def parse_think_output(text: str) -> ThinkOutput | None:
    """Parse and validate sub-agent output into ThinkOutput model.

    Args:
        text: Raw sub-agent output text.

    Returns:
        Validated ThinkOutput or None if parsing fails.
    """
    data = extract_json_from_text(text)
    if not data:
        return None
    try:
        return ThinkOutput.model_validate(data)
    except Exception:
        return None


def format_research_summary(output: ThinkOutput, thesis_title: str | None = None) -> str:
    """Format ThinkOutput as a readable Telegram message for the user.

    Args:
        output: Parsed ThinkOutput from sub-agent.
        thesis_title: Optional thesis title for context.

    Returns:
        Formatted message string.
    """
    lines: list[str] = []

    header = "🔬 Research complete"
    if thesis_title:
        header += f": {thesis_title}"
    lines.append(header)
    lines.append("")

    # Research summary (truncated)
    summary = output.research_summary[:500]
    lines.append(f"📋 **Summary**\n{summary}")
    lines.append("")

    # Critic assessment
    critic = output.critic_assessment[:300]
    lines.append(f"⚠️ **Critic**\n{critic}")
    lines.append("")

    # Ticker recommendations
    if output.ticker_recommendations:
        lines.append("📊 **Ticker Recs**")
        for rec in output.ticker_recommendations:
            emoji = {"add": "➕", "remove": "➖", "watch": "👀"}.get(
                rec.action, "•"
            )
            lines.append(f"  {emoji} {rec.symbol} ({rec.action}): {rec.reasoning[:80]}")
        lines.append("")

    # Conviction change
    if output.conviction_change:
        cc = output.conviction_change
        old_raw = cc.old_value or 0
        old = int(old_raw * 100) if old_raw <= 1 else int(old_raw)
        new = int(cc.new_value * 100) if cc.new_value <= 1 else int(cc.new_value)
        direction = "📈" if new > old else "📉" if new < old else "➡️"
        lines.append(
            f"{direction} **Conviction**: {old}% → {new}%\n"
            f"Reason: {cc.reasoning[:150]}"
        )
        lines.append("")

    # Thesis update
    if output.thesis_update:
        tu = output.thesis_update
        lines.append("📝 **Thesis Update Proposed**")
        if tu.title:
            lines.append(f"  Title: {tu.title}")
        if tu.status:
            lines.append(f"  Status: {tu.status}")
        if tu.description:
            lines.append(f"  Description: {tu.description[:150]}...")

    return "\n".join(lines)


def apply_research_to_db(
    engine: ThoughtsEngine,
    thesis_id: int,
    output: ThinkOutput,
    session_id: int | None = None,
) -> dict[str, Any]:
    """Apply research output to the database.

    Auto-applies: research summary save, session completion,
    ticker recommendations as notes.
    Requires approval: conviction changes, thesis status changes.

    Args:
        engine: ThoughtsEngine instance.
        thesis_id: The thesis being researched.
        output: Parsed ThinkOutput.
        session_id: Optional session ID to mark complete.

    Returns:
        Dict with applied changes and pending approvals.
    """
    applied: list[str] = []
    pending: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    # Save research summary as a journal entry
    engine.create_journal(
        title=f"Research session — {now[:10]}",
        content=output.research_summary,
        journal_type="research",
        thesis_id=thesis_id,
    )
    applied.append("Research summary saved as journal entry")

    # Save critic assessment as a thought/note
    engine.add_thought(
        content=f"[Critic] {output.critic_assessment}",
        linked_thesis_id=thesis_id,
    )
    applied.append("Critic assessment saved as note")

    # Save ticker recommendations as notes
    for rec in output.ticker_recommendations:
        engine.add_thought(
            content=(
                f"[Ticker Rec] {rec.symbol} — {rec.action}: "
                f"{rec.reasoning}"
            ),
            linked_thesis_id=thesis_id,
            linked_symbol=rec.symbol,
        )
        applied.append(f"Ticker rec saved: {rec.symbol} ({rec.action})")

    # Mark session complete
    if session_id:
        engine.complete_session(
            session_id,
            summary=output.research_summary[:200],
        )
        applied.append(f"Session #{session_id} marked complete")

    # Queue conviction change for approval
    if output.conviction_change:
        cc = output.conviction_change
        new_val = cc.new_value
        # Normalize to 0-100 scale
        if new_val <= 1:
            new_val = new_val * 100
        pending.append({
            "type": "conviction_change",
            "thesis_id": thesis_id,
            "old_value": cc.old_value,
            "new_value": new_val,
            "reasoning": cc.reasoning,
        })

    # Queue thesis update for approval
    if output.thesis_update:
        tu = output.thesis_update
        pending.append({
            "type": "thesis_update",
            "thesis_id": thesis_id,
            "title": tu.title,
            "description": tu.description,
            "status": tu.status,
        })

    return {"applied": applied, "pending": pending}


def apply_conviction_change(
    engine: ThoughtsEngine,
    thesis_id: int,
    new_conviction: float,
) -> bool:
    """Apply an approved conviction change to the moves DB.

    Args:
        engine: ThoughtsEngine instance.
        thesis_id: Thesis to update.
        new_conviction: New conviction value (0-100 scale).

    Returns:
        True if successful.
    """
    return engine.update_thesis_conviction(thesis_id, new_conviction)


def apply_thesis_update(
    engine: ThoughtsEngine,
    thesis_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> bool:
    """Apply an approved thesis update to the moves DB.

    Args:
        engine: ThoughtsEngine instance.
        thesis_id: Thesis to update.
        title: New title if changed.
        description: New description if changed.
        status: New status if changed.

    Returns:
        True if successful.
    """
    return engine.update_thesis(
        thesis_id, title=title, description=description, status=status
    )
