"""Tests for the NewsValidator.

Tests RSS parsing, article scoring, auto-transition logic, stale detection,
and sell signal generation on invalidation using mocked HTTP responses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from engine import SignalAction, SignalSource, ThesisStatus
from engine.news_validator import NewsValidator
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
    <title>NVIDIA reports record AI chip revenue surge</title>
    <link>https://example.com/article1</link>
    <source>Reuters</source>
    <pubDate>Mon, 06 Jan 2026 12:00:00 GMT</pubDate>
</item>
<item>
    <title>Hyperscaler capex spending increases dramatically</title>
    <link>https://example.com/article2</link>
    <source>Bloomberg</source>
    <pubDate>Sun, 05 Jan 2026 10:00:00 GMT</pubDate>
</item>
<item>
    <title>Weather forecast for this weekend</title>
    <link>https://example.com/article3</link>
    <source>Weather.com</source>
    <pubDate>Sat, 04 Jan 2026 08:00:00 GMT</pubDate>
</item>
</channel>
</rss>
"""


@pytest.fixture
def engines(seeded_db):
    """Create ThesisEngine, SignalEngine, and NewsValidator."""
    thesis_engine = ThesisEngine(seeded_db)
    signal_engine = SignalEngine(seeded_db)

    # Update thesis with validation/failure criteria
    thesis_engine.update_thesis(
        1,
        validation_criteria=["capex spending increases", "AI chip revenue surge"],
        failure_criteria=["capex cuts announced", "AI spending decline"],
    )

    validator = NewsValidator(seeded_db, thesis_engine, signal_engine)
    return {
        "db": seeded_db,
        "thesis_engine": thesis_engine,
        "signal_engine": signal_engine,
        "validator": validator,
    }


class TestParseRSS:
    """Tests for RSS parsing."""

    def test_parse_rss_extracts_articles(self, engines):
        """RSS XML is correctly parsed into article dicts."""
        articles = engines["validator"]._parse_rss(SAMPLE_RSS)
        assert len(articles) == 3
        assert articles[0]["title"] == "NVIDIA reports record AI chip revenue surge"
        assert articles[0]["source"] == "Reuters"
        assert articles[1]["url"] == "https://example.com/article2"


class TestScoreArticle:
    """Tests for article sentiment scoring."""

    def test_supporting_article(self, engines):
        """Articles matching validation criteria score as supporting."""
        thesis = engines["thesis_engine"].get_thesis(1)
        article = {"title": "AI chip revenue surge continues in Q4"}
        assert engines["validator"].score_article(article, thesis) == "supporting"

    def test_contradicting_article(self, engines):
        """Articles matching failure criteria score as contradicting."""
        thesis = engines["thesis_engine"].get_thesis(1)
        article = {"title": "Major capex cuts announced across hyperscalers"}
        assert engines["validator"].score_article(article, thesis) == "contradicting"

    def test_neutral_article(self, engines):
        """Articles matching neither criteria score as neutral."""
        thesis = engines["thesis_engine"].get_thesis(1)
        article = {"title": "Weather forecast for this weekend"}
        assert engines["validator"].score_article(article, thesis) == "neutral"


class TestAutoTransition:
    """Tests for automatic thesis status transitions."""

    def test_strengthening_on_supporting(self, engines):
        """3+ supporting, 0 contradicting transitions to strengthening."""
        db = engines["db"]
        now = datetime.now(UTC).isoformat()
        for i in range(3):
            db.execute(
                """INSERT INTO thesis_news
                   (thesis_id, headline, sentiment, timestamp)
                   VALUES (1, ?, 'supporting', ?)""",
                (f"Good news {i}", now),
            )
        db.connect().commit()

        result = engines["validator"].auto_transition(1, 3, 0)
        assert result == "strengthening"

        thesis = engines["thesis_engine"].get_thesis(1)
        assert thesis.status == ThesisStatus.STRENGTHENING

    def test_weakening_on_contradicting(self, engines):
        """3+ contradicting, 0 supporting transitions to weakening."""
        db = engines["db"]
        now = datetime.now(UTC).isoformat()
        for i in range(3):
            db.execute(
                """INSERT INTO thesis_news
                   (thesis_id, headline, sentiment, timestamp)
                   VALUES (1, ?, 'contradicting', ?)""",
                (f"Bad news {i}", now),
            )
        db.connect().commit()

        result = engines["validator"].auto_transition(1, 0, 3)
        assert result == "weakening"

    def test_invalidation_from_weakening(self, engines):
        """Weakening thesis + 2 more contradicting → invalidated."""
        # First transition to weakening
        engines["thesis_engine"].transition_status(1, ThesisStatus.WEAKENING, reason="test")

        db = engines["db"]
        now = datetime.now(UTC).isoformat()
        for i in range(2):
            db.execute(
                """INSERT INTO thesis_news
                   (thesis_id, headline, sentiment, timestamp)
                   VALUES (1, ?, 'contradicting', ?)""",
                (f"More bad news {i}", now),
            )
        db.connect().commit()

        result = engines["validator"].auto_transition(1, 0, 2)
        assert result == "invalidated"

    def test_no_transition_on_mixed(self, engines):
        """Mixed evidence does not trigger transition."""
        result = engines["validator"].auto_transition(1, 2, 2)
        assert result is None

    def test_invalidation_generates_sell_signals(self, engines):
        """Invalidation generates SELL signals for linked positions."""
        db = engines["db"]
        # Add a position linked to thesis 1
        db.execute(
            """INSERT INTO positions (account_id, symbol, shares, avg_cost, thesis_id)
               VALUES (1, 'NVDA', 100, 130.0, 1)"""
        )
        db.connect().commit()

        # Transition to weakening first
        engines["thesis_engine"].transition_status(1, ThesisStatus.WEAKENING, reason="test")

        now = datetime.now(UTC).isoformat()
        for i in range(2):
            db.execute(
                """INSERT INTO thesis_news
                   (thesis_id, headline, sentiment, timestamp)
                   VALUES (1, ?, 'contradicting', ?)""",
                (f"Bad {i}", now),
            )
        db.connect().commit()

        engines["validator"].auto_transition(1, 0, 2)

        signals = engines["signal_engine"].list_signals(symbol="NVDA")
        sell_signals = [s for s in signals if s.action == SignalAction.SELL]
        assert len(sell_signals) >= 1
        assert sell_signals[0].source == SignalSource.NEWS_EVENT


class TestCheckStale:
    """Tests for stale thesis detection."""

    def test_finds_stale_theses(self, engines):
        """Theses with no recent news are flagged as stale."""
        stale = engines["validator"].check_stale(max_age_days=0)
        # Thesis 1 has no news yet, should be stale
        assert len(stale) >= 1
        assert stale[0]["thesis_id"] == 1

    def test_fresh_thesis_not_stale(self, engines):
        """Theses with recent news are not stale."""
        db = engines["db"]
        db.execute(
            """INSERT INTO thesis_news
               (thesis_id, headline, sentiment, timestamp)
               VALUES (1, 'Fresh news', 'neutral', datetime('now'))"""
        )
        db.connect().commit()

        stale = engines["validator"].check_stale(max_age_days=1)
        thesis_ids = [s["thesis_id"] for s in stale]
        assert 1 not in thesis_ids


class TestScoreNewsItem:
    """Tests for the detailed news scoring function."""

    def test_score_supporting_item(self, engines):
        """Supporting items get positive score with matched keywords."""
        thesis = engines["thesis_engine"].get_thesis(1)
        result = engines["validator"].score_news_item(
            headline="AI chip revenue surge continues",
            url="https://example.com/1",
            summary="NVIDIA reports record quarterly earnings driven by AI demand",
            thesis=thesis,
        )
        assert result["sentiment"] == "supporting"
        assert result["score"] > 0
        assert len(result["matched_keywords"]) > 0

    def test_score_contradicting_item(self, engines):
        """Contradicting items are identified correctly."""
        thesis = engines["thesis_engine"].get_thesis(1)
        result = engines["validator"].score_news_item(
            headline="Major capex cuts announced by hyperscalers",
            url="https://example.com/2",
            summary="Cloud companies slash spending amid economic downturn",
            thesis=thesis,
        )
        assert result["sentiment"] == "contradicting"

    def test_score_neutral_item(self, engines):
        """Unrelated items score as neutral with low score."""
        thesis = engines["thesis_engine"].get_thesis(1)
        result = engines["validator"].score_news_item(
            headline="Weather forecast for the weekend",
            url="https://example.com/3",
            summary="Expect sunny skies across the northeast",
            thesis=thesis,
        )
        assert result["sentiment"] == "neutral"
        assert result["score"] < 0.5

    def test_score_includes_summary_text(self, engines):
        """Summary text is used in scoring, not just headline."""
        thesis = engines["thesis_engine"].get_thesis(1)
        # Headline is generic but summary has keywords
        result = engines["validator"].score_news_item(
            headline="Quarterly earnings report",
            url="https://example.com/4",
            summary="AI chip revenue surge and capex spending increases in Q4",
            thesis=thesis,
        )
        assert result["sentiment"] == "supporting"


class TestScoreNewsBatch:
    """Tests for batch scoring of externally-provided news."""

    def test_batch_scores_and_stores(self, engines):
        """Batch scoring stores results in thesis_news."""
        items = [
            {"headline": "AI chip revenue surge", "url": "https://a.com/1", "summary": ""},
            {"headline": "Random news", "url": "https://a.com/2", "summary": ""},
        ]
        results = engines["validator"].score_news_batch(1, items)
        assert len(results) == 2

        rows = engines["db"].fetchall("SELECT * FROM thesis_news WHERE thesis_id = 1")
        assert len(rows) == 2

    def test_batch_deduplicates_by_url(self, engines):
        """Duplicate URLs are skipped in batch scoring."""
        items = [
            {"headline": "AI chip revenue surge", "url": "https://a.com/1", "summary": ""},
        ]
        engines["validator"].score_news_batch(1, items)
        results2 = engines["validator"].score_news_batch(1, items)
        assert len(results2) == 0

    def test_batch_nonexistent_thesis(self, engines):
        """Returns empty for nonexistent thesis."""
        items = [{"headline": "x", "url": "y", "summary": ""}]
        results = engines["validator"].score_news_batch(999, items)
        assert results == []


class TestValidateThesis:
    """Tests for the full validation cycle."""

    @patch("engine.news_validator.NewsValidator._fetch_rss")
    def test_validate_stores_articles(self, mock_fetch, engines):
        """Validation stores scored articles in thesis_news."""
        mock_fetch.return_value = [
            {
                "title": "AI chip revenue surge Q4",
                "url": "https://a.com/1",
                "source": "Reuters",
                "published": "",
            },
            {
                "title": "Random unrelated news",
                "url": "https://a.com/2",
                "source": "BBC",
                "published": "",
            },
        ]

        result = engines["validator"].validate_thesis(1)
        assert result["thesis_id"] == 1
        assert result["supporting"] >= 1

        rows = engines["db"].fetchall("SELECT * FROM thesis_news WHERE thesis_id = 1")
        assert len(rows) >= 1

    def test_validate_nonexistent_thesis(self, engines):
        """Validating a nonexistent thesis returns error."""
        result = engines["validator"].validate_thesis(999)
        assert result.get("error") == "not_found"
