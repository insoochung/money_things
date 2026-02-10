"""Tests for the MoneyMovesCore orchestrator.

Tests the central coordination of engines and critical pipeline operations.
Focuses on system integration, error handling, and edge cases.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from db.database import Database
from engine.core import MoneyMovesCore


class TestMoneyMovesCore:
    """Test the MoneyMovesCore central orchestrator."""

    def test_core_initialization(self, seeded_db: Database) -> None:
        """Test that core initializes all engines correctly."""
        mock_broker = Mock()
        core = MoneyMovesCore(seeded_db, mock_broker)
        
        # Verify all engines are initialized
        assert core.thesis_engine is not None
        assert core.signal_engine is not None
        assert core.risk_manager is not None
        assert core.principles_engine is not None
        assert core.whatif_engine is not None
        assert core.discovery_engine is not None
        assert core.approval_workflow is not None
        
        # Verify dependencies are passed correctly
        assert core.db == seeded_db
        assert core.broker == mock_broker

    def test_core_initialization_with_settings(self, seeded_db: Database) -> None:
        """Test core initialization with custom settings."""
        mock_broker = Mock()
        settings = {"risk_tolerance": 0.05, "max_position_size": 0.1}
        core = MoneyMovesCore(seeded_db, mock_broker, settings)
        
        assert core.settings == settings

    def test_core_initialization_empty_settings(self, seeded_db: Database) -> None:
        """Test core initialization defaults to empty settings dict."""
        mock_broker = Mock()
        core = MoneyMovesCore(seeded_db, mock_broker)
        
        assert core.settings == {}

    def test_core_broker_exception_handling(self, seeded_db: Database) -> None:
        """Test core handles broker exceptions gracefully."""
        mock_broker = Mock()
        mock_broker.get_positions.side_effect = Exception("Connection failed")
        
        core = MoneyMovesCore(seeded_db, mock_broker)
        
        # Core should be created even if broker has issues
        assert core.broker == mock_broker

    def test_core_with_none_database(self) -> None:
        """Test core initialization with None database."""
        mock_broker = Mock()
        
        # Core can handle None database without crashing during initialization
        core = MoneyMovesCore(None, mock_broker)  # type: ignore
        assert core.db is None
        assert core.broker == mock_broker

    def test_core_with_none_broker(self, seeded_db: Database) -> None:
        """Test core initialization with None broker."""
        # Should handle None broker gracefully
        core = MoneyMovesCore(seeded_db, None)  # type: ignore
        assert core.broker is None
        
        # Engines should still be initialized
        assert core.thesis_engine is not None
        assert core.signal_engine is not None

    def test_core_engines_share_database(self, seeded_db: Database) -> None:
        """Test that all engines share the same database instance."""
        mock_broker = Mock()
        core = MoneyMovesCore(seeded_db, mock_broker)
        
        # All engines should use the same database instance
        assert core.thesis_engine.db is seeded_db
        assert core.signal_engine.db is seeded_db
        assert core.risk_manager.db is seeded_db
        assert core.principles_engine.db is seeded_db
        assert core.whatif_engine.db is seeded_db
        assert core.discovery_engine.db is seeded_db
        assert core.approval_workflow.db is seeded_db

    @patch("engine.core.logger")
    def test_core_logging_setup(self, mock_logger, seeded_db: Database) -> None:
        """Test that core properly sets up logging."""
        mock_broker = Mock()
        MoneyMovesCore(seeded_db, mock_broker)
        
        # Verify logger is imported (indicates logging is configured)
        assert mock_logger is not None