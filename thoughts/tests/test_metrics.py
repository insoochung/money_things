"""Tests for utils/metrics.py -- decision quality metrics."""

from __future__ import annotations

from pathlib import Path

import pytest
from utils.metrics import (
    bootstrap_metrics,
    calculate_calibration,
    calculate_pass_accuracy,
    calculate_timeframe_accuracy,
    calculate_win_rate,
    load_history_ideas,
    parse_frontmatter,
    parse_idea_file,
    parse_timeframe,
)


@pytest.fixture()
def history_dir(tmp_path: Path) -> Path:
    """Create a temporary history directory with test idea files."""
    history = tmp_path / "history" / "ideas"
    history.mkdir(parents=True)
    return history


def _write_idea(
    directory: Path,
    filename: str,
    *,
    symbol: str = "AAPL",
    status: str = "acted",
    action: str = "buy",
    conviction: str = "high",
    outcome: str = "win",
    created: str = "2026-01-01",
    closed: str = "2026-02-01",
    timeframe: str = "3 months",
    price_at_pass: str = "",
    pass_reason: str = "",
) -> None:
    content = f"""---
symbol: {symbol}
status: {status}
action: {action}
conviction: {conviction}
outcome: {outcome}
created: {created}
closed: {closed}
timeframe: {timeframe}
price_at_pass: {price_at_pass}
pass_reason: {pass_reason}
---

# {symbol} {action}

Test idea content.
"""
    (directory / filename).write_text(content)


class TestParseFrontmatter:
    def test_parses_basic_frontmatter(self) -> None:
        content = """---
symbol: AAPL
status: acted
conviction: high
---
# Content
"""
        fm = parse_frontmatter(content)
        assert fm["symbol"] == "AAPL"
        assert fm["status"] == "acted"

    def test_handles_quoted_values(self) -> None:
        content = """---
title: "AI inference shift"
symbol: 'AMD'
---
"""
        fm = parse_frontmatter(content)
        assert fm["title"] == "AI inference shift"
        assert fm["symbol"] == "AMD"

    def test_returns_empty_for_no_frontmatter(self) -> None:
        fm = parse_frontmatter("No frontmatter here")
        assert fm == {}


class TestParseIdeaFile:
    def test_parses_idea_file(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md")
        result = parse_idea_file(str(history_dir / "001-AAPL-buy.md"))
        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["status"] == "acted"

    def test_returns_none_for_missing(self) -> None:
        result = parse_idea_file("/nonexistent/path.md")
        assert result is None


class TestLoadHistoryIdeas:
    def test_loads_all_ideas(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md")
        _write_idea(history_dir, "002-MSFT-buy.md", symbol="MSFT")
        ideas = load_history_ideas(str(history_dir))
        assert len(ideas) == 2

    def test_returns_empty_for_missing_dir(self) -> None:
        ideas = load_history_ideas("/nonexistent/path")
        assert ideas == []


class TestCalculateWinRate:
    def test_basic_win_rate(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md", outcome="win")
        _write_idea(history_dir, "002-MSFT-buy.md", symbol="MSFT", outcome="loss")
        _write_idea(history_dir, "003-GOOG-buy.md", symbol="GOOG", outcome="win")
        result = calculate_win_rate(str(history_dir))
        assert result["total_acted"] == 3
        assert result["wins"] == 2
        assert result["losses"] == 1
        assert result["win_rate"] == 66.7

    def test_empty_history(self, history_dir: Path) -> None:
        result = calculate_win_rate(str(history_dir))
        assert result["total_acted"] == 0
        assert result["win_rate"] is None

    def test_by_conviction(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md", conviction="high", outcome="win")
        _write_idea(history_dir, "002-MSFT-buy.md", symbol="MSFT", conviction="low", outcome="loss")
        result = calculate_win_rate(str(history_dir))
        assert result["by_conviction"]["high"]["wins"] == 1
        assert result["by_conviction"]["low"]["acted"] == 1

    def test_ignores_passed_ideas(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md", status="acted", outcome="win")
        _write_idea(history_dir, "002-MSFT-buy.md", symbol="MSFT", status="passed")
        result = calculate_win_rate(str(history_dir))
        assert result["total_acted"] == 1


class TestCalculatePassAccuracy:
    def test_basic_accuracy(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md", status="passed", price_at_pass="100.0")
        _write_idea(
            history_dir,
            "002-MSFT-buy.md",
            symbol="MSFT",
            status="passed",
            price_at_pass="400.0",
        )
        current_prices = {"AAPL": 90.0, "MSFT": 450.0}
        result = calculate_pass_accuracy(str(history_dir), current_prices)
        assert result["total_passed"] == 2
        assert result["correct"] == 1  # AAPL went down
        assert result["incorrect"] == 1  # MSFT went up >5%

    def test_no_current_prices(self, history_dir: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md", status="passed", price_at_pass="100.0")
        result = calculate_pass_accuracy(str(history_dir))
        assert result["unknown"] == 1


class TestParseTimeframe:
    def test_months(self) -> None:
        assert parse_timeframe("3 months") == 90
        assert parse_timeframe("6 mo") == 180

    def test_weeks(self) -> None:
        assert parse_timeframe("2 weeks") == 14

    def test_years(self) -> None:
        assert parse_timeframe("1 year") == 365
        assert parse_timeframe("2 yrs") == 730

    def test_days(self) -> None:
        assert parse_timeframe("30 days") == 30

    def test_invalid(self) -> None:
        assert parse_timeframe("") is None
        assert parse_timeframe("soon") is None


class TestCalculateTimeframeAccuracy:
    def test_basic_timeframe(self, history_dir: Path) -> None:
        _write_idea(
            history_dir,
            "001-AAPL-buy.md",
            created="2026-01-01",
            closed="2026-03-01",
            timeframe="2 months",
        )
        result = calculate_timeframe_accuracy(str(history_dir))
        assert result["total"] == 1
        bucket = result["by_timeframe"]["1-3 months"]
        assert bucket["count"] == 1


class TestCalculateCalibration:
    def test_insufficient_data(self, history_dir: Path) -> None:
        result = calculate_calibration(str(history_dir))
        assert result["overall_calibration"] == "insufficient data"


class TestBootstrapMetrics:
    def test_generates_file(self, history_dir: Path, tmp_path: Path) -> None:
        _write_idea(history_dir, "001-AAPL-buy.md", outcome="win")
        output = tmp_path / "metrics.md"
        bootstrap_metrics(str(history_dir), str(output))
        assert output.exists()
        content = output.read_text()
        assert "Decision Metrics" in content
        assert "Win rate" in content
