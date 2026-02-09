"""Onboarding module for bootstrapping the research agent with user context.

Generates an investor profile from existing data (moves DB, old journals)
and optional interview answers.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bridge import ThoughtsBridge
from engine import ThoughtsEngine

DATA_DIR = Path(__file__).parent / "data"
PROFILE_PATH = DATA_DIR / "investor_profile.md"
OLD_JOURNAL_DIR = Path.home() / "workspace" / "money_journal"
PORTFOLIO_MD = Path(__file__).parent / "portfolio.md"

INTERVIEW_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "investing_style",
        "question": "What's your investing style? (growth/value/momentum/mixed)",
    },
    {
        "id": "time_horizon",
        "question": "Typical holding period? (days/weeks/months/years)",
    },
    {
        "id": "sectors",
        "question": "Sectors or industries you know well or are interested in?",
    },
    {
        "id": "risk_tolerance",
        "question": "Risk tolerance? (conservative/moderate/aggressive)",
    },
    {
        "id": "convictions",
        "question": "Any strong market convictions right now?",
    },
    {
        "id": "avoid",
        "question": "Anything you explicitly want to avoid? (sectors, strategies, etc.)",
    },
    {
        "id": "goal",
        "question": "Primary goal? (wealth building/income/speculation/learning)",
    },
    {
        "id": "experience",
        "question": "Investing experience level? (beginner/intermediate/advanced)",
    },
]


class OnboardingEngine:
    """Bootstraps the research agent with user context.

    Args:
        thoughts_engine: ThoughtsEngine instance.
        bridge: ThoughtsBridge instance.
    """

    def __init__(
        self,
        thoughts_engine: ThoughtsEngine | None = None,
        bridge: ThoughtsBridge | None = None,
    ) -> None:
        self.engine = thoughts_engine or ThoughtsEngine()
        self.bridge = bridge or ThoughtsBridge(self.engine)

    def generate_profile_from_history(self) -> str:
        """Import context from moves DB and old journal files.

        Reads active theses, current positions, old journal files,
        and portfolio.md to build a baseline profile.

        Returns:
            Markdown profile summary.
        """
        sections: list[str] = ["# Investor Profile (Auto-Generated)", ""]
        sections.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d')}*\n")

        # Active theses
        theses = self.engine.get_theses()
        active = [t for t in theses if t.get("status") == "active"]
        if active:
            sections.append("## Active Theses")
            for t in active:
                symbols = _parse_symbols(t.get("symbols", "[]"))
                conv = round((t.get("conviction") or 0) * 100)
                sym_str = ", ".join(symbols) or "no symbols"
                sections.append(
                    f"- **{t['title']}** (conviction: {conv}%) — {sym_str}"
                )
            sections.append("")

        # Current positions
        positions = self.engine.get_positions()
        held = [p for p in positions if (p.get("shares") or 0) > 0]
        if held:
            sections.append("## Current Positions")
            for p in held:
                sections.append(
                    f"- {p['symbol']}: {p['shares']:.0f} shares "
                    f"@ ${p.get('avg_cost', 0):.2f} ({p.get('side', 'long')})"
                )
            sections.append("")

        # Old journal files
        if OLD_JOURNAL_DIR.exists():
            journal_files = sorted(OLD_JOURNAL_DIR.glob("*.md"))[:10]
            if journal_files:
                sections.append("## Historical Journal Excerpts")
                for jf in journal_files:
                    content = jf.read_text(errors="replace")[:500]
                    sections.append(f"### {jf.stem}")
                    sections.append(content.strip())
                    sections.append("")

        # Portfolio.md
        if PORTFOLIO_MD.exists():
            sections.append("## Portfolio Notes")
            sections.append(PORTFOLIO_MD.read_text(errors="replace")[:2000].strip())
            sections.append("")

        return "\n".join(sections)

    def get_interview_questions(self) -> list[dict[str, str]]:
        """Return onboarding questions with IDs.

        Returns:
            List of dicts with 'id' and 'question' keys.
        """
        return list(INTERVIEW_QUESTIONS)

    def process_answers(self, answers: dict[str, str]) -> str:
        """Generate an investor profile markdown from interview answers.

        Args:
            answers: Dict mapping question IDs to answer strings.

        Returns:
            Markdown profile section.
        """
        lines = ["## Investor Interview", ""]
        label_map = {q["id"]: q["question"].split("?")[0] for q in INTERVIEW_QUESTIONS}
        for q in INTERVIEW_QUESTIONS:
            answer = answers.get(q["id"], "—")
            lines.append(f"**{label_map[q['id']]}:** {answer}")
        lines.append("")
        return "\n".join(lines)

    def save_profile(self, profile: str) -> None:
        """Save the investor profile to data/investor_profile.md.

        Also updates AGENT_PROMPT.md to reference the profile.

        Args:
            profile: Markdown profile content.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(profile, encoding="utf-8")
        _ensure_agent_prompt_reference()

    def get_combined_profile(self, answers: dict[str, str] | None = None) -> str:
        """Combine history import and interview answers into a final profile.

        Args:
            answers: Optional interview answers. If None, only history is used.

        Returns:
            Complete markdown profile.
        """
        profile = self.generate_profile_from_history()
        if answers:
            profile += "\n" + self.process_answers(answers)
        return profile


def _parse_symbols(symbols_raw: str | list) -> list[str]:
    """Safely parse a symbols field that may be JSON or already a list."""
    if isinstance(symbols_raw, list):
        return symbols_raw
    try:
        parsed = json.loads(symbols_raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _ensure_agent_prompt_reference() -> None:
    """Add investor profile reference to AGENT_PROMPT.md if missing."""
    prompt_path = Path(__file__).parent / "AGENT_PROMPT.md"
    marker = "## Investor Profile"
    if prompt_path.exists():
        content = prompt_path.read_text(encoding="utf-8")
        if marker in content:
            return
    else:
        content = ""

    section = (
        "\n\n## Investor Profile\n"
        "Read ~/workspace/money/thoughts/data/investor_profile.md "
        "at the start of every session.\n"
        "This contains the user's investing style, risk tolerance, "
        "sector expertise, and convictions.\n"
        "Tailor all research and recommendations to this profile.\n"
    )
    content += section
    prompt_path.write_text(content, encoding="utf-8")
