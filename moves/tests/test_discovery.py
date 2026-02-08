"""Tests for the DiscoveryEngine and get_sector helper."""

from __future__ import annotations

import json

from db.database import Database
from engine.discovery import SECTOR_MAP, DiscoveryEngine, get_sector


class TestGetSector:
    """Test the get_sector helper function."""

    def test_known_ticker_returns_sector(self) -> None:
        """Known tickers should return their mapped sector."""
        assert get_sector("AAPL") == "Technology"
        assert get_sector("AMZN") == "Consumer Cyclical"
        assert get_sector("VST") == "Utilities"

    def test_unknown_ticker_returns_unknown(self) -> None:
        """Unknown tickers should return 'Unknown'."""
        assert get_sector("XYZ") == "Unknown"
        assert get_sector("FAKE") == "Unknown"

    def test_case_insensitive(self) -> None:
        """get_sector should handle lowercase input."""
        assert get_sector("aapl") == "Technology"
        assert get_sector("nvda") == "Technology"

    def test_all_mapped_tickers_return_sector(self) -> None:
        """Every ticker in SECTOR_MAP should return a non-Unknown sector."""
        for symbol in SECTOR_MAP:
            assert get_sector(symbol) != "Unknown"


class TestSearchKeyword:
    """Test the internal keyword-to-ticker mapping."""

    def test_ai_keyword(self) -> None:
        """AI keyword returns GPU and cloud companies."""
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        results = engine._search_keyword("AI")
        assert "NVDA" in results
        assert "AMD" in results

    def test_case_insensitive_keyword(self) -> None:
        """Keywords should match case-insensitively."""
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        assert engine._search_keyword("ai") == engine._search_keyword("AI")
        assert engine._search_keyword("Cloud") == engine._search_keyword("cloud")

    def test_unknown_keyword_returns_empty(self) -> None:
        """Unknown keywords should return an empty list."""
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        assert engine._search_keyword("biotech") == []
        assert engine._search_keyword("") == []

    def test_ev_keyword(self) -> None:
        """EV keyword returns TSLA."""
        engine = DiscoveryEngine.__new__(DiscoveryEngine)
        assert engine._search_keyword("EV") == ["TSLA"]


class TestScanUniverse:
    """Test full universe scanning with database interaction."""

    def test_scan_finds_tickers_from_thesis_keywords(self, seeded_db: Database) -> None:
        """Scan should discover tickers matching thesis universe_keywords."""
        # Update thesis with universe_keywords
        seeded_db.execute(
            "UPDATE theses SET universe_keywords = ? WHERE id = 1",
            (json.dumps(["AI"]),),
        )
        seeded_db.connect().commit()

        engine = DiscoveryEngine(seeded_db)
        discoveries = engine.scan_universe()

        symbols = [d["symbol"] for d in discoveries]
        assert "NVDA" in symbols
        assert all(d["thesis_id"] == 1 for d in discoveries)
        assert all("reason" in d for d in discoveries)

    def test_scan_excludes_existing_positions(self, seeded_db: Database) -> None:
        """Tickers already in portfolio should not appear as discoveries."""
        # Add NVDA as existing position
        seeded_db.execute(
            "INSERT INTO positions (account_id, symbol, shares, avg_cost) "
            "VALUES (1, 'NVDA', 10, 130.0)"
        )
        seeded_db.execute(
            "UPDATE theses SET universe_keywords = ? WHERE id = 1",
            (json.dumps(["AI"]),),
        )
        seeded_db.connect().commit()

        engine = DiscoveryEngine(seeded_db)
        discoveries = engine.scan_universe()

        symbols = [d["symbol"] for d in discoveries]
        assert "NVDA" not in symbols

    def test_scan_no_keywords_returns_empty(self, seeded_db: Database) -> None:
        """Thesis without universe_keywords should yield no discoveries."""
        engine = DiscoveryEngine(seeded_db)
        discoveries = engine.scan_universe()
        assert discoveries == []

    def test_scan_deduplicates_across_keywords(self, seeded_db: Database) -> None:
        """Same ticker from multiple keywords should appear only once."""
        seeded_db.execute(
            "UPDATE theses SET universe_keywords = ? WHERE id = 1",
            (json.dumps(["AI", "semiconductors"]),),
        )
        seeded_db.connect().commit()

        engine = DiscoveryEngine(seeded_db)
        discoveries = engine.scan_universe()

        symbols = [d["symbol"] for d in discoveries]
        # NVDA appears in both AI and semiconductors but should only be listed once
        assert symbols.count("NVDA") == 1

    def test_scan_only_active_theses(self, seeded_db: Database) -> None:
        """Only active/strengthening theses should be scanned."""
        seeded_db.execute(
            "UPDATE theses SET status = 'archived', universe_keywords = ? WHERE id = 1",
            (json.dumps(["AI"]),),
        )
        seeded_db.connect().commit()

        engine = DiscoveryEngine(seeded_db)
        discoveries = engine.scan_universe()
        assert discoveries == []
