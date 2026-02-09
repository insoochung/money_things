"""Tests for the onboarding module."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import ThoughtsEngine
from onboard import OnboardingEngine


@pytest.fixture()
def setup(tmp_path: Path):
    """Create test DBs and engine."""
    moves_db = tmp_path / "moves.db"
    conn = sqlite3.connect(str(moves_db))
    conn.executescript("""
        CREATE TABLE theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, status TEXT DEFAULT 'active',
            symbols TEXT DEFAULT '[]', conviction REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            horizon TEXT DEFAULT ''
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, symbol TEXT, shares REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0, side TEXT DEFAULT 'long',
            thesis_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY, action TEXT, symbol TEXT,
            thesis_id INTEGER, confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'manual', reasoning TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, symbol TEXT, action TEXT,
            shares REAL, price REAL,
            timestamp TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO theses (title, symbols, conviction)
            VALUES ('AI inference', '["AMD","NVDA"]', 0.8);
        INSERT INTO positions (symbol, shares, avg_cost, side) VALUES ('AMD', 50, 120.50, 'long');
    """)
    conn.commit()
    conn.close()

    engine = ThoughtsEngine(thoughts_db=tmp_path / "thoughts.db", moves_db=moves_db)
    from bridge import ThoughtsBridge

    bridge = ThoughtsBridge(engine)
    onboard = OnboardingEngine(engine, bridge)
    return onboard, tmp_path


def test_generate_profile_from_history(setup):
    """Profile generation includes theses and positions."""
    onboard, _ = setup
    profile = onboard.generate_profile_from_history()
    assert "AI inference" in profile
    assert "AMD" in profile
    assert "50 shares" in profile


def test_get_interview_questions(setup):
    """Questions have correct structure."""
    onboard, _ = setup
    questions = onboard.get_interview_questions()
    assert len(questions) == 8
    assert all("id" in q and "question" in q for q in questions)
    ids = [q["id"] for q in questions]
    assert "investing_style" in ids
    assert "risk_tolerance" in ids


def test_process_answers(setup):
    """Answers are formatted into markdown."""
    onboard, _ = setup
    answers = {
        "investing_style": "Growth",
        "time_horizon": "Months",
        "sectors": "Tech, AI",
        "risk_tolerance": "Moderate",
        "convictions": "AI will grow",
        "avoid": "Crypto",
        "goal": "Wealth building",
        "experience": "Intermediate",
    }
    result = onboard.process_answers(answers)
    assert "Growth" in result
    assert "Moderate" in result
    assert "Investor Interview" in result


def test_save_profile(setup):
    """Profile is saved to disk."""
    onboard, tmp_path = setup
    # Patch paths to use tmp_path
    import onboard as onboard_mod

    orig_data = onboard_mod.DATA_DIR
    orig_profile = onboard_mod.PROFILE_PATH
    onboard_mod.DATA_DIR = tmp_path / "data"
    onboard_mod.PROFILE_PATH = tmp_path / "data" / "investor_profile.md"

    try:
        with patch.object(onboard_mod, "_ensure_agent_prompt_reference"):
            onboard.save_profile("# Test Profile\nHello")
        saved = (tmp_path / "data" / "investor_profile.md").read_text()
        assert "Test Profile" in saved
    finally:
        onboard_mod.DATA_DIR = orig_data
        onboard_mod.PROFILE_PATH = orig_profile


def test_combined_profile_without_answers(setup):
    """Combined profile works without interview answers."""
    onboard, _ = setup
    profile = onboard.get_combined_profile()
    assert "AI inference" in profile
    assert "Investor Interview" not in profile


def test_combined_profile_with_answers(setup):
    """Combined profile includes both history and answers."""
    onboard, _ = setup
    answers = {"investing_style": "Value", "time_horizon": "Years"}
    profile = onboard.get_combined_profile(answers)
    assert "AI inference" in profile
    assert "Value" in profile
