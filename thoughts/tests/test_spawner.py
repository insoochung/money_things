"""Tests for spawner module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from spawner import (
    ConvictionChange,
    ThesisUpdate,
    ThinkOutput,
    TickerRec,
    build_task,
    get_output_schema,
    load_agent_prompt,
)


class TestModels:
    def test_think_output_minimal(self) -> None:
        """ThinkOutput works with only required fields."""
        output = ThinkOutput(
            research_summary="Findings here",
            critic_assessment="Risks here",
        )
        assert output.thesis_update is None
        assert output.conviction_change is None
        assert output.ticker_recommendations == []

    def test_think_output_full(self) -> None:
        """ThinkOutput accepts all fields."""
        output = ThinkOutput(
            research_summary="Summary",
            thesis_update=ThesisUpdate(
                title="New title", status="strengthening"
            ),
            ticker_recommendations=[
                TickerRec(
                    symbol="CRWD", action="add",
                    reasoning="Market leader",
                )
            ],
            critic_assessment="Could fail if...",
            conviction_change=ConvictionChange(
                old_value=0.5, new_value=0.75,
                reasoning="Evidence supports",
            ),
        )
        data = output.model_dump()
        assert data["thesis_update"]["status"] == "strengthening"
        assert len(data["ticker_recommendations"]) == 1
        assert data["conviction_change"]["new_value"] == 0.75

    def test_conviction_bounds(self) -> None:
        """ConvictionChange rejects out-of-range values."""
        with pytest.raises(Exception):
            ConvictionChange(new_value=1.5, reasoning="too high")
        with pytest.raises(Exception):
            ConvictionChange(new_value=-0.1, reasoning="too low")

    def test_think_output_roundtrip_json(self) -> None:
        """ThinkOutput can serialize and deserialize."""
        output = ThinkOutput(
            research_summary="Test",
            critic_assessment="Risks",
            ticker_recommendations=[
                TickerRec(symbol="AMD", action="watch", reasoning="wait")
            ],
        )
        json_str = output.model_dump_json()
        restored = ThinkOutput.model_validate_json(json_str)
        assert restored.research_summary == "Test"
        assert restored.ticker_recommendations[0].symbol == "AMD"


class TestLoadPrompt:
    def test_loads_from_file(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "AGENT_PROMPT.md"
        prompt_file.write_text("# Test Prompt\nDo research.")
        result = load_agent_prompt(prompt_file)
        assert "Test Prompt" in result

    def test_fallback_when_missing(self, tmp_path: Path) -> None:
        result = load_agent_prompt(tmp_path / "nonexistent.md")
        assert "investment research" in result.lower()


class TestGetOutputSchema:
    def test_returns_valid_json(self) -> None:
        schema = get_output_schema()
        parsed = json.loads(schema)
        assert "properties" in parsed
        assert "research_summary" in parsed["properties"]


class TestBuildTask:
    def test_contains_all_sections(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("# Agent Prompt")

        gates = {
            "session_count": 1,
            "meets_session_minimum": False,
            "meets_conviction_threshold": False,
            "cooldown_ok": False,
            "can_generate_signals": False,
        }

        task = build_task(
            idea="AI cybersecurity",
            context_packet="## Thesis details here",
            gates=gates,
            agent_prompt_path=prompt,
        )

        assert "Agent Prompt" in task
        assert "AI cybersecurity" in task
        assert "Thesis details here" in task
        assert "Slow-to-Act" in task
        assert "Required Output Format" in task
        assert "```json" in task

    def test_gates_reflected_in_task(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("# Prompt")

        gates_pass = {
            "session_count": 3,
            "meets_session_minimum": True,
            "meets_conviction_threshold": True,
            "cooldown_ok": True,
            "can_generate_signals": True,
        }
        task = build_task("idea", "ctx", gates_pass, prompt)
        assert "Sessions completed: 3" in task
        assert "Can generate signals: YES" in task

    def test_gates_fail_in_task(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("# Prompt")

        gates_fail = {
            "session_count": 0,
            "meets_session_minimum": False,
            "meets_conviction_threshold": False,
            "cooldown_ok": False,
            "can_generate_signals": False,
        }
        task = build_task("idea", "ctx", gates_fail, prompt)
        assert "Can generate signals: NO" in task
        assert "focus on research" in task.lower()
