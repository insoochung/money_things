"""Build sub-agent task strings for /think sessions.

Combines the AGENT_PROMPT.md template, context packet, and user idea
into a complete task string that can be passed to OpenClaw's sessions_spawn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

AGENT_PROMPT_PATH = Path(__file__).parent / "AGENT_PROMPT.md"


class ThinkOutput(BaseModel):
    """Expected output schema from a /think sub-agent session.

    The sub-agent must produce JSON matching this structure.
    """

    research_summary: str = Field(
        description="Key findings from research (2-5 paragraphs)"
    )
    thesis_update: ThesisUpdate | None = Field(
        default=None,
        description="Proposed thesis changes, if any",
    )
    ticker_recommendations: list[TickerRec] = Field(
        default_factory=list,
        description="Tickers to add/remove from thesis",
    )
    critic_assessment: str = Field(
        description="Devil's advocate: risks, challenges, what could go wrong"
    )
    conviction_change: ConvictionChange | None = Field(
        default=None,
        description="Proposed conviction change, if warranted",
    )


class ThesisUpdate(BaseModel):
    """Proposed changes to a thesis."""

    title: str | None = Field(
        default=None, description="New title, if changed"
    )
    description: str | None = Field(
        default=None, description="Updated thesis description"
    )
    status: str | None = Field(
        default=None,
        description=(
            "strengthening | weakening | confirmed | invalidated"
        ),
    )


class TickerRec(BaseModel):
    """A ticker recommendation from the sub-agent."""

    symbol: str
    action: str = Field(
        description="add | remove | watch"
    )
    reasoning: str


class ConvictionChange(BaseModel):
    """Proposed conviction score change."""

    old_value: float | None = None
    new_value: float = Field(ge=0.0, le=1.0)
    reasoning: str


def load_agent_prompt(path: Path | None = None) -> str:
    """Load the AGENT_PROMPT.md template.

    Args:
        path: Override path for testing.

    Returns:
        The prompt template string.
    """
    prompt_path = path or AGENT_PROMPT_PATH
    if prompt_path.exists():
        return prompt_path.read_text()
    return "You are an investment research agent."


def get_output_schema() -> str:
    """Return the expected JSON output format as a string spec.

    Uses the ThinkOutput Pydantic model to generate the schema.
    """
    return json.dumps(
        ThinkOutput.model_json_schema(), indent=2
    )


def build_task(
    idea: str,
    context_packet: str,
    gates: dict[str, Any],
    agent_prompt_path: Path | None = None,
) -> str:
    """Build the complete task string for a /think sub-agent.

    Combines: agent prompt + context + idea + output format + gates.

    Args:
        idea: The user's raw idea or thesis reference.
        context_packet: Formatted context string from context_builder.
        gates: Slow-to-act gate statuses from context_builder.
        agent_prompt_path: Override path for agent prompt (testing).

    Returns:
        Complete task string ready for sessions_spawn.
    """
    prompt = load_agent_prompt(agent_prompt_path)

    gates_section = _format_gates_section(gates)
    output_section = _format_output_section()

    return (
        f"{prompt}\n\n"
        f"---\n\n"
        f"# Research Task\n\n"
        f"**User's idea:** {idea}\n\n"
        f"---\n\n"
        f"# Context\n\n"
        f"{context_packet}\n\n"
        f"---\n\n"
        f"{gates_section}\n\n"
        f"---\n\n"
        f"{output_section}"
    )


def _format_gates_section(gates: dict[str, Any]) -> str:
    """Format slow-to-act gates for the task string."""
    lines = [
        "# Slow-to-Act Gates",
        "",
        "Before recommending any trades or signals, check these gates:",
        "",
        f"- Sessions completed: {gates.get('session_count', 0)} "
        f"(minimum 2 required)",
        f"- Meets session minimum: "
        f"{'YES' if gates.get('meets_session_minimum') else 'NO'}",
        f"- Meets conviction threshold (≥70%): "
        f"{'YES' if gates.get('meets_conviction_threshold') else 'NO'}",
        f"- Cooldown passed (1 week): "
        f"{'YES' if gates.get('cooldown_ok') else 'NO'}",
        f"- Can generate signals: "
        f"{'YES' if gates.get('can_generate_signals') else 'NO'}",
        "",
        "If gates are NOT met, focus on research and thesis development.",
        "Do NOT recommend trades until all gates pass.",
    ]
    return "\n".join(lines)


def _format_output_section() -> str:
    """Format the expected output section for the task string."""
    example = {
        "research_summary": "Key findings...",
        "thesis_update": {
            "title": None,
            "description": "Updated description...",
            "status": "strengthening",
        },
        "ticker_recommendations": [
            {
                "symbol": "CRWD",
                "action": "add",
                "reasoning": "Market leader in...",
            }
        ],
        "critic_assessment": "Key risks include...",
        "conviction_change": {
            "old_value": 0.6,
            "new_value": 0.75,
            "reasoning": "Evidence supports...",
        },
    }

    lines = [
        "# Required Output Format",
        "",
        "End your session with a JSON block wrapped in ```json fences:",
        "",
        "```json",
        json.dumps(example, indent=2),
        "```",
        "",
        "All fields are required except thesis_update and "
        "conviction_change (omit if no change).",
        "ticker_recommendations can be an empty list.",
    ]
    return "\n".join(lines)
