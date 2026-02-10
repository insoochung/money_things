"""Tests for the NewsScanner wrapper class.

Tests the news scanning functionality that wraps NewsValidator
for scheduled execution.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from db.database import Database
from engine.news_scanner import NewsScanner


class TestNewsScanner:
    """Test the NewsScanner class."""

    def test_news_scanner_initialization(self, seeded_db: Database) -> None:
        """Test NewsScanner initializes with required dependencies."""
        mock_thesis_engine = Mock()
        mock_signal_engine = Mock()

        with patch("engine.news_scanner.NewsValidator") as mock_validator_class:
            scanner = NewsScanner(seeded_db, mock_thesis_engine, mock_signal_engine)

            # Verify NewsValidator is created with correct parameters
            mock_validator_class.assert_called_once_with(
                db=seeded_db,
                thesis_engine=mock_thesis_engine,
                signal_engine=mock_signal_engine,
            )

            assert scanner.validator == mock_validator_class.return_value

    def test_run_scan_success(self, seeded_db: Database) -> None:
        """Test successful news scan execution."""
        mock_thesis_engine = Mock()
        mock_signal_engine = Mock()

        with patch("engine.news_scanner.NewsValidator") as mock_validator_class:
            mock_validator = Mock()
            mock_validator_class.return_value = mock_validator

            # Mock validation results
            mock_results = [
                {"thesis_id": 1, "score": 0.8, "transition": False},
                {"thesis_id": 2, "score": 0.2, "transition": True, "new_status": "invalidated"},
            ]
            mock_validator.validate_all.return_value = mock_results

            scanner = NewsScanner(seeded_db, mock_thesis_engine, mock_signal_engine)

            with patch("engine.news_scanner.logger") as mock_logger:
                results = scanner.run_scan()

                assert results == mock_results
                mock_validator.validate_all.assert_called_once()

                # Verify logging
                mock_logger.info.assert_called()
                assert mock_logger.info.call_count == 2  # Start and completion

    def test_run_scan_no_results(self, seeded_db: Database) -> None:
        """Test news scan with empty results."""
        mock_thesis_engine = Mock()
        mock_signal_engine = Mock()

        with patch("engine.news_scanner.NewsValidator") as mock_validator_class:
            mock_validator = Mock()
            mock_validator_class.return_value = mock_validator
            mock_validator.validate_all.return_value = []

            scanner = NewsScanner(seeded_db, mock_thesis_engine, mock_signal_engine)

            with patch("engine.news_scanner.logger") as mock_logger:
                results = scanner.run_scan()

                assert results == []
                mock_validator.validate_all.assert_called_once()
                mock_logger.info.assert_called()

    def test_run_scan_validator_exception(self, seeded_db: Database) -> None:
        """Test news scan when NewsValidator raises exception."""
        mock_thesis_engine = Mock()
        mock_signal_engine = Mock()

        with patch("engine.news_scanner.NewsValidator") as mock_validator_class:
            mock_validator = Mock()
            mock_validator_class.return_value = mock_validator
            mock_validator.validate_all.side_effect = Exception("Validation failed")

            scanner = NewsScanner(seeded_db, mock_thesis_engine, mock_signal_engine)

            # Should propagate the exception (scheduler handles it)
            with pytest.raises(Exception, match="Validation failed"):
                scanner.run_scan()

    def test_news_scanner_with_none_engines(self, seeded_db: Database) -> None:
        """Test NewsScanner handles None engines gracefully."""
        with patch("engine.news_scanner.NewsValidator") as mock_validator_class:
            NewsScanner(seeded_db, None, None)  # type: ignore

            # NewsValidator should still be created
            mock_validator_class.assert_called_once_with(
                db=seeded_db,
                thesis_engine=None,
                signal_engine=None,
            )
