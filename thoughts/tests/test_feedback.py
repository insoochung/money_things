"""Tests for the feedback module — sub-agent output parsing and DB application."""

from __future__ import annotations

import json

from feedback import (
    apply_research_to_db,
    extract_json_from_text,
    format_research_summary,
    parse_think_output,
)
from spawner import ThinkOutput


class TestExtractJson:
    """Test JSON extraction from raw text."""

    def test_fenced_json_block(self) -> None:
        text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        result = extract_json_from_text(text)
        assert result == {"key": "value"}

    def test_raw_json_object(self) -> None:
        text = 'Here is the result: {"research_summary": "test"}'
        result = extract_json_from_text(text)
        assert result is not None
        assert result["research_summary"] == "test"

    def test_multiple_json_takes_last(self) -> None:
        text = '{"first": true} and then {"second": true}'
        result = extract_json_from_text(text)
        assert result == {"second": True}

    def test_no_json_returns_none(self) -> None:
        assert extract_json_from_text("no json here") is None

    def test_nested_json(self) -> None:
        data = {
            "research_summary": "test",
            "thesis_update": {"title": "new", "status": "strengthening"},
        }
        text = f"Result:\n```json\n{json.dumps(data)}\n```"
        result = extract_json_from_text(text)
        assert result["thesis_update"]["title"] == "new"


class TestParseThinkOutput:
    """Test parsing raw text into ThinkOutput model."""

    def test_valid_output(self) -> None:
        data = {
            "research_summary": "META beat earnings.",
            "critic_assessment": "Capex risk is real.",
            "ticker_recommendations": [],
        }
        text = f"```json\n{json.dumps(data)}\n```"
        result = parse_think_output(text)
        assert result is not None
        assert result.research_summary == "META beat earnings."

    def test_with_conviction_change(self) -> None:
        data = {
            "research_summary": "Strong results.",
            "critic_assessment": "Some risks.",
            "ticker_recommendations": [
                {"symbol": "AVGO", "action": "add", "reasoning": "Custom silicon"},
            ],
            "conviction_change": {
                "old_value": 0.65,
                "new_value": 0.75,
                "reasoning": "Earnings beat",
            },
        }
        text = f"```json\n{json.dumps(data)}\n```"
        result = parse_think_output(text)
        assert result is not None
        assert result.conviction_change is not None
        assert result.conviction_change.new_value == 0.75
        assert len(result.ticker_recommendations) == 1

    def test_invalid_json_returns_none(self) -> None:
        assert parse_think_output("not json at all") is None

    def test_invalid_schema_returns_none(self) -> None:
        text = '```json\n{"wrong_field": true}\n```'
        assert parse_think_output(text) is None


class TestFormatResearchSummary:
    """Test formatting ThinkOutput for Telegram display."""

    def test_basic_format(self) -> None:
        output = ThinkOutput(
            research_summary="META Q4 was strong.",
            critic_assessment="Capex could hurt margins.",
            ticker_recommendations=[],
        )
        msg = format_research_summary(output, "META thesis")
        assert "Research complete" in msg
        assert "META thesis" in msg
        assert "META Q4 was strong" in msg
        assert "Capex could hurt" in msg

    def test_with_conviction_change(self) -> None:
        output = ThinkOutput(
            research_summary="Good results.",
            critic_assessment="Risks noted.",
            ticker_recommendations=[],
            conviction_change={
                "old_value": 0.6,
                "new_value": 0.75,
                "reasoning": "Beat estimates",
            },
        )
        msg = format_research_summary(output)
        assert "Conviction" in msg
        assert "Beat estimates" in msg

    def test_with_ticker_recs(self) -> None:
        output = ThinkOutput(
            research_summary="Analyzed sector.",
            critic_assessment="Concentration risk.",
            ticker_recommendations=[
                {"symbol": "CRWD", "action": "add", "reasoning": "Market leader"},
            ],
        )
        msg = format_research_summary(output)
        assert "CRWD" in msg
        assert "add" in msg


class TestApplyResearchToDb:
    """Test applying research output to the database."""

    def test_applies_journal_and_notes(self, tmp_path) -> None:
        """Verify research saves journal entry and critic note."""
        from engine import ThoughtsEngine

        thoughts_db = tmp_path / "thoughts.db"
        moves_db = tmp_path / "moves.db"

        # Create minimal moves DB with a thesis
        import sqlite3

        conn = sqlite3.connect(str(moves_db))
        conn.execute(
            "CREATE TABLE theses (id INTEGER PRIMARY KEY, title TEXT, "
            "thesis_text TEXT, strategy TEXT, status TEXT, symbols TEXT, "
            "conviction REAL, horizon TEXT, validation_criteria TEXT, "
            "failure_criteria TEXT, source_module TEXT, created_at TEXT, "
            "updated_at TEXT, user_id TEXT, universe_keywords TEXT)"
        )
        conn.execute(
            "INSERT INTO theses (id, title, status, symbols, conviction) "
            "VALUES (1, 'Test thesis', 'active', 'META', 85)"
        )
        # Create other required tables
        for table in ["positions", "signals", "trades"]:
            conn.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"
            )
        conn.commit()
        conn.close()

        engine = ThoughtsEngine(
            thoughts_db=thoughts_db, moves_db=moves_db
        )

        output = ThinkOutput(
            research_summary="META earnings were strong.",
            critic_assessment="Capex risk remains.",
            ticker_recommendations=[
                {"symbol": "AVGO", "action": "watch", "reasoning": "Custom silicon"},
            ],
        )

        result = apply_research_to_db(engine, thesis_id=1, output=output)

        assert len(result["applied"]) >= 3  # journal + critic + ticker rec
        assert "Research summary saved" in result["applied"][0]

        # Verify journal entry was created
        journals = engine.list_journals()
        assert len(journals) == 1
        assert "Research session" in journals[0]["title"]

        # Verify thoughts/notes were created
        thoughts = engine.list_thoughts()
        assert len(thoughts) >= 2  # critic + ticker rec

    def test_queues_conviction_change(self, tmp_path) -> None:
        """Conviction changes go to pending, not auto-applied."""
        from engine import ThoughtsEngine

        thoughts_db = tmp_path / "thoughts.db"
        moves_db = tmp_path / "moves.db"

        import sqlite3

        conn = sqlite3.connect(str(moves_db))
        conn.execute(
            "CREATE TABLE theses (id INTEGER PRIMARY KEY, title TEXT, "
            "thesis_text TEXT, strategy TEXT, status TEXT, symbols TEXT, "
            "conviction REAL, horizon TEXT, validation_criteria TEXT, "
            "failure_criteria TEXT, source_module TEXT, created_at TEXT, "
            "updated_at TEXT, user_id TEXT, universe_keywords TEXT)"
        )
        conn.execute(
            "INSERT INTO theses (id, title, status, symbols, conviction) "
            "VALUES (1, 'Test', 'active', 'META', 85)"
        )
        for table in ["positions", "signals", "trades"]:
            conn.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"
            )
        conn.commit()
        conn.close()

        engine = ThoughtsEngine(
            thoughts_db=thoughts_db, moves_db=moves_db
        )

        output = ThinkOutput(
            research_summary="Strong quarter.",
            critic_assessment="Some risks.",
            ticker_recommendations=[],
            conviction_change={
                "old_value": 0.85,
                "new_value": 0.90,
                "reasoning": "Beat estimates",
            },
        )

        result = apply_research_to_db(engine, thesis_id=1, output=output)

        assert len(result["pending"]) == 1
        assert result["pending"][0]["type"] == "conviction_change"
        assert result["pending"][0]["new_value"] == 90  # normalized to 0-100
