"""Metrics computation for investment journal.

All metrics are computed from history/ideas/ files.
Never LLM-generated -- always verified computation.

This module provides functions for analysing the historical record of
investment ideas in the money_thoughts system. It reads markdown files from
the ``history/ideas/`` directory, parses their YAML frontmatter, and computes
various quantitative metrics about decision quality.

The metrics are designed to support the feedback loop in the money system:
after ideas are acted upon or passed, their outcomes are recorded, and this
module computes aggregate statistics that help the user refine their thesis
development and conviction calibration over time.

Key functions:
    - ``calculate_win_rate`` -- Overall and by-conviction win/loss ratio
    - ``calculate_calibration`` -- How well conviction levels predict outcomes
    - ``calculate_pass_accuracy`` -- Were "pass" decisions correct in hindsight?
    - ``calculate_timeframe_accuracy`` -- Stated vs actual holding periods
    - ``analyze_by_theme`` -- Theme-level analysis (stub, not yet implemented)
    - ``bootstrap_metrics`` -- Generate a complete ``metrics.md`` file

Data flow:
    ``history/ideas/*.md`` --> ``parse_frontmatter`` --> ``parse_idea_file``
    --> ``load_history_ideas`` --> various ``calculate_*`` functions
    --> ``bootstrap_metrics`` writes ``metrics.md``

Idea file format (YAML frontmatter):
    Each markdown file in ``history/ideas/`` has YAML frontmatter with fields
    like ``symbol``, ``created``, ``status`` (acted/passed), ``action``
    (buy/sell), ``timeframe``, ``conviction`` (high/medium/low), ``closed``,
    ``outcome`` (win/loss/partial/pending), ``pass_reason``, ``price_at_pass``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter from a markdown file's content string.

    Looks for content delimited by ``---`` markers at the start of the string.
    Parses simple ``key: value`` pairs (one per line) and returns them as a
    flat dict. Handles quoted values (both single and double quotes) by
    stripping the quotes. Lines without a colon or with an empty value after
    the colon are silently skipped.

    This is a lightweight parser that does NOT use a full YAML library,
    which means it only supports flat key-value pairs (no nested structures,
    lists, or multi-line values). This is sufficient for the idea file format
    used in money_thoughts.

    Parameters:
        content: The full text content of a markdown file. The frontmatter
            must start at the very beginning of the string with ``---``.

    Returns:
        A dict mapping frontmatter keys to their string values. Returns an
        empty dict if no valid frontmatter is found (no opening ``---``,
        no closing ``---``, or empty frontmatter block).

    Examples:
        >>> parse_frontmatter("---\\nsymbol: AAPL\\nstatus: acted\\n---\\n# My Idea")
        {'symbol': 'AAPL', 'status': 'acted'}
        >>> parse_frontmatter("No frontmatter here")
        {}
    """
    frontmatter: dict[str, str] = {}
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            fm_text = content[3:end].strip()
            for line in fm_text.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    if value == "":
                        continue
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    frontmatter[key] = value
    return frontmatter


def parse_idea_file(filepath: str) -> dict[str, str] | None:
    """Parse a single idea markdown file and extract its frontmatter metadata.

    Opens the file at the given path, reads its content, extracts the YAML
    frontmatter via ``parse_frontmatter``, and returns a dict with all the
    relevant metadata fields. Fields that are not present in the frontmatter
    will have empty-string values in the returned dict.

    Parameters:
        filepath: Absolute or relative path to a markdown idea file
            (e.g. ``"history/ideas/001-AAPL-buy.md"``).

    Returns:
        A dict with the following keys (all string values):
            - ``filepath``: The original path passed in
            - ``symbol``: Stock ticker (e.g. ``"AAPL"``)
            - ``created``: Creation date in ``YYYY-MM-DD`` format
            - ``status``: ``"acted"`` or ``"passed"``
            - ``action``: ``"buy"`` or ``"sell"``
            - ``timeframe``: Stated holding period (e.g. ``"3 months"``)
            - ``conviction``: ``"high"``, ``"medium"``, or ``"low"``
            - ``closed``: Close date in ``YYYY-MM-DD`` format (if closed)
            - ``outcome``: ``"win"``, ``"loss"``, ``"partial"``, or ``"pending"``
            - ``pass_reason``: Why the idea was passed (if status is passed)
            - ``price_at_pass``: Price when the idea was passed

        Returns ``None`` if the file cannot be read or has no valid
        frontmatter.

    Side effects:
        - Reads one file from disk.
    """
    try:
        with open(filepath) as f:
            content = f.read()

        fm = parse_frontmatter(content)
        if not fm:
            return None

        return {
            "filepath": filepath,
            "symbol": fm.get("symbol", ""),
            "created": fm.get("created", ""),
            "status": fm.get("status", ""),
            "action": fm.get("action", ""),
            "timeframe": fm.get("timeframe", ""),
            "conviction": fm.get("conviction", ""),
            "closed": fm.get("closed", ""),
            "outcome": fm.get("outcome", ""),
            "pass_reason": fm.get("pass_reason", ""),
            "price_at_pass": fm.get("price_at_pass", ""),
        }
    except Exception:
        return None


def load_history_ideas(history_path: str) -> list[dict[str, str]]:
    """Load and parse all idea files from the history/ideas/ directory.

    Scans the given directory for ``*.md`` files, parses each one via
    ``parse_idea_file``, and returns the successfully parsed results. Files
    that fail to parse (no frontmatter, read errors) are silently skipped.

    Parameters:
        history_path: Path to the ``history/ideas/`` directory containing
            archived idea markdown files.

    Returns:
        A list of dicts, each in the format returned by ``parse_idea_file``.
        Empty list if the directory does not exist or contains no parseable
        idea files.

    Side effects:
        - Reads files from disk (one per ``*.md`` file in the directory).
    """
    ideas: list[dict[str, str]] = []
    history_dir = Path(history_path)

    if not history_dir.exists():
        return ideas

    for filepath in history_dir.glob("*.md"):
        idea = parse_idea_file(str(filepath))
        if idea:
            ideas.append(idea)

    return ideas


def calculate_win_rate(history_path: str) -> dict[str, Any]:
    """Calculate win rate from acted ideas in the history directory.

    Loads all idea files, filters to those with ``status == "acted"``, and
    tallies wins, losses, partial wins, and pending outcomes. Also breaks
    down win rates by conviction level (high/medium/low).

    Win rate is defined as: wins / (wins + losses) * 100. Partial outcomes
    and pending outcomes are tracked but excluded from the win rate
    calculation.

    Parameters:
        history_path: Path to the ``history/ideas/`` directory.

    Returns:
        A dict with the following structure:
            - ``total_acted`` (int): Number of ideas with status "acted"
            - ``wins`` (int): Count of ideas with outcome "win"
            - ``losses`` (int): Count of ideas with outcome "loss"
            - ``pending`` (int): Count of ideas with no resolved outcome
            - ``partial`` (int): Count of ideas with outcome "partial"
            - ``win_rate`` (float | None): Overall win percentage, or None
              if no wins+losses to calculate from
            - ``by_conviction`` (dict): Nested dict with keys "high",
              "medium", "low", each containing:
                - ``acted`` (int): Number of acted ideas at this conviction
                - ``wins`` (int): Number of wins at this conviction
                - ``rate`` (float | None): Win rate for this conviction level

    Side effects:
        - Reads idea files from disk (via ``load_history_ideas``).
    """
    ideas = load_history_ideas(history_path)
    acted = [t for t in ideas if t["status"] == "acted"]

    result: dict[str, Any] = {
        "total_acted": len(acted),
        "wins": 0,
        "losses": 0,
        "pending": 0,
        "partial": 0,
        "win_rate": None,
        "by_conviction": {
            "high": {"acted": 0, "wins": 0, "rate": None},
            "medium": {"acted": 0, "wins": 0, "rate": None},
            "low": {"acted": 0, "wins": 0, "rate": None},
        },
    }

    for idea in acted:
        outcome = idea.get("outcome", "pending").lower()
        conviction = idea.get("conviction", "medium").lower()

        if outcome == "win":
            result["wins"] += 1
        elif outcome == "loss":
            result["losses"] += 1
        elif outcome == "partial":
            result["partial"] += 1
        else:
            result["pending"] += 1

        if conviction in result["by_conviction"]:
            result["by_conviction"][conviction]["acted"] += 1
            if outcome == "win":
                result["by_conviction"][conviction]["wins"] += 1

    decided = result["wins"] + result["losses"]
    if decided > 0:
        result["win_rate"] = round(result["wins"] / decided * 100, 1)

    for level in result["by_conviction"]:
        conv_data = result["by_conviction"][level]
        if conv_data["acted"] > 0:
            conv_data["rate"] = round(conv_data["wins"] / conv_data["acted"] * 100, 1)

    return result


def calculate_pass_accuracy(
    history_path: str, current_prices: dict[str, float] | None = None
) -> dict[str, Any]:
    """Calculate accuracy of pass decisions by comparing price at pass to current price.

    A pass was "correct" if the price went down or stayed roughly flat
    (within +5%) after the idea was passed. A pass was "incorrect" (i.e.
    a missed opportunity) if the price rose more than 5%.

    This supports the feedback loop: if passes are frequently incorrect,
    the user may be too conservative in their idea filtering.

    Parameters:
        history_path: Path to the ``history/ideas/`` directory.
        current_prices: Dict mapping ticker symbols to their current prices
            (e.g. ``{"AAPL": 185.50, "MSFT": 420.00}``). If ``None`` or
            empty, all passed ideas are classified as "unknown" and no
            accuracy is computed.

    Returns:
        A dict with:
            - ``total_passed`` (int): Total ideas with status "passed"
            - ``correct`` (int): Passes where price change <= +5%
            - ``incorrect`` (int): Passes where price change > +5%
            - ``unknown`` (int): Passes that cannot be evaluated (missing
              price_at_pass, symbol not in current_prices, etc.)
            - ``accuracy`` (float | None): correct / (correct + incorrect) * 100
            - ``details`` (list[dict]): Per-idea detail with symbol,
              price_at_pass, current price, and change percentage

    Side effects:
        - Reads idea files from disk (via ``load_history_ideas``).
    """
    ideas = load_history_ideas(history_path)
    passed = [t for t in ideas if t["status"] == "passed"]

    result: dict[str, Any] = {
        "total_passed": len(passed),
        "correct": 0,
        "incorrect": 0,
        "unknown": 0,
        "accuracy": None,
        "details": [],
    }

    if not current_prices:
        result["unknown"] = len(passed)
        return result

    for idea in passed:
        symbol = idea.get("symbol", "")
        price_at_pass_str = idea.get("price_at_pass", "")

        try:
            price_at_pass = float(price_at_pass_str)
        except (ValueError, TypeError):
            result["unknown"] += 1
            continue

        if symbol not in current_prices:
            result["unknown"] += 1
            continue

        current_price = current_prices[symbol]
        change_pct = (current_price - price_at_pass) / price_at_pass * 100

        detail = {
            "symbol": symbol,
            "price_at_pass": price_at_pass,
            "current": current_price,
            "change_pct": round(change_pct, 1),
        }
        result["details"].append(detail)

        if change_pct <= 5:
            result["correct"] += 1
        else:
            result["incorrect"] += 1

    evaluated = result["correct"] + result["incorrect"]
    if evaluated > 0:
        result["accuracy"] = round(result["correct"] / evaluated * 100, 1)

    return result


def calculate_calibration(history_path: str) -> dict[str, Any]:
    """Calculate calibration -- how well conviction levels predict outcomes.

    Compares the user's stated conviction levels against actual win rates
    to determine whether the user is well-calibrated, overconfident, or
    underconfident. Expected ranges:

    - High conviction: should win 70%+ of the time
    - Medium conviction: should win 50-70% of the time
    - Low conviction: should win 30-50% of the time

    If actual win rates fall within these ranges, the conviction level is
    considered "calibrated". The overall calibration assessment requires at
    least two conviction levels with sufficient data to evaluate.

    Parameters:
        history_path: Path to the ``history/ideas/`` directory.

    Returns:
        A dict with keys ``"high"``, ``"medium"``, ``"low"``, each containing:
            - ``expected`` (str): Expected win rate range (e.g. ``"70%+"``)
            - ``actual`` (float | None): Actual win rate, or None if no data
            - ``calibrated`` (bool | None): Whether actual falls in expected
              range, or None if insufficient data

        Plus:
            - ``overall_calibration`` (str): One of:
              ``"well-calibrated"``, ``"inverse correlation - review conviction
              assessment"``, ``"possibly underconfident"``,
              ``"possibly overconfident"``, or ``"insufficient data"``

    Side effects:
        - Reads idea files from disk (via ``calculate_win_rate``).
    """
    win_data = calculate_win_rate(history_path)
    by_conviction = win_data["by_conviction"]

    result: dict[str, Any] = {
        "high": {
            "expected": "70%+",
            "actual": by_conviction["high"]["rate"],
            "calibrated": None,
        },
        "medium": {
            "expected": "50-70%",
            "actual": by_conviction["medium"]["rate"],
            "calibrated": None,
        },
        "low": {
            "expected": "30-50%",
            "actual": by_conviction["low"]["rate"],
            "calibrated": None,
        },
        "overall_calibration": "insufficient data",
    }

    if result["high"]["actual"] is not None:
        result["high"]["calibrated"] = result["high"]["actual"] >= 70

    if result["medium"]["actual"] is not None:
        result["medium"]["calibrated"] = 50 <= result["medium"]["actual"] <= 70

    if result["low"]["actual"] is not None:
        result["low"]["calibrated"] = 30 <= result["low"]["actual"] <= 50

    calibrations = [
        result[level]["calibrated"]
        for level in ["high", "medium", "low"]
        if result[level]["calibrated"] is not None
    ]

    if len(calibrations) >= 2:
        if all(calibrations):
            result["overall_calibration"] = "well-calibrated"
        elif by_conviction["high"]["rate"] and by_conviction["low"]["rate"]:
            if by_conviction["high"]["rate"] < by_conviction["low"]["rate"]:
                result["overall_calibration"] = "inverse correlation - review conviction assessment"
            elif by_conviction["high"]["rate"] > 70:
                result["overall_calibration"] = "possibly underconfident"
            else:
                result["overall_calibration"] = "possibly overconfident"

    return result


def parse_timeframe(timeframe_str: str) -> int | None:
    """Parse a human-readable timeframe string into a number of days.

    Supports common time units: day(s), week(s), month(s)/mo, year(s)/yr.
    The input format is ``"<number> <unit>"`` (e.g. ``"3 months"``,
    ``"2 weeks"``, ``"1 year"``).

    Conversion factors:
        - 1 week = 7 days
        - 1 month = 30 days
        - 1 year = 365 days

    Parameters:
        timeframe_str: A human-readable timeframe string. Case-insensitive.
            Leading/trailing whitespace is stripped.

    Returns:
        int: The timeframe converted to days, or ``None`` if the string
        cannot be parsed (empty string, unrecognised format, etc.).

    Examples:
        >>> parse_timeframe("3 months")
        90
        >>> parse_timeframe("2 weeks")
        14
        >>> parse_timeframe("")
        None
    """
    if not timeframe_str:
        return None

    timeframe_str = timeframe_str.lower().strip()

    match = re.match(r"(\d+)\s*(day|week|month|mo|year|yr)s?", timeframe_str)
    if match:
        num = int(match.group(1))
        unit = match.group(2)

        if unit in ("day",):
            return num
        elif unit in ("week",):
            return num * 7
        elif unit in ("month", "mo"):
            return num * 30
        elif unit in ("year", "yr"):
            return num * 365

    return None


def calculate_timeframe_accuracy(history_path: str) -> dict[str, Any]:
    """Calculate how accurately stated timeframes predicted actual holding periods.

    For each acted idea that has both a creation date and a close date,
    computes the actual holding period in days and compares it to the stated
    timeframe. Results are bucketed by stated timeframe duration:

    - < 1 month (< 30 days stated)
    - 1-3 months (30-89 days stated)
    - 3-6 months (90-179 days stated)
    - 6-12 months (180-364 days stated)
    - > 12 months (365+ days stated)

    Within each bucket, accuracy is assessed by the ratio of average actual
    days to average stated days:
    - 0.8-1.2 ratio: "accurate"
    - < 0.8 ratio: "faster than stated"
    - > 1.2 ratio: "slower than stated"

    Parameters:
        history_path: Path to the ``history/ideas/`` directory.

    Returns:
        A dict with:
            - ``total`` (int): Number of acted ideas with both created and
              closed dates
            - ``by_timeframe`` (dict): Keyed by bucket name, each containing:
                - ``count`` (int): Number of ideas in this bucket
                - ``avg_actual_days`` (float | None): Average actual holding
                  period in days
                - ``avg_stated_days`` (float | None): Average stated timeframe
                  in days
                - ``accuracy`` (str): ``"accurate"``, ``"faster than stated"``,
                  ``"slower than stated"``, or ``"N/A"``

    Side effects:
        - Reads idea files from disk (via ``load_history_ideas``).
    """
    ideas = load_history_ideas(history_path)
    acted = [t for t in ideas if t["status"] == "acted" and t.get("closed") and t.get("created")]

    result: dict[str, Any] = {
        "total": len(acted),
        "by_timeframe": {
            "< 1 month": {"count": 0, "total_actual_days": 0, "total_stated_days": 0},
            "1-3 months": {"count": 0, "total_actual_days": 0, "total_stated_days": 0},
            "3-6 months": {"count": 0, "total_actual_days": 0, "total_stated_days": 0},
            "6-12 months": {"count": 0, "total_actual_days": 0, "total_stated_days": 0},
            "> 12 months": {"count": 0, "total_actual_days": 0, "total_stated_days": 0},
        },
    }

    for idea in acted:
        stated_days = parse_timeframe(idea.get("timeframe", ""))
        if stated_days is None:
            continue

        try:
            created = datetime.strptime(idea["created"], "%Y-%m-%d").date()
            closed = datetime.strptime(idea["closed"], "%Y-%m-%d").date()
            actual_days = (closed - created).days
        except (ValueError, TypeError):
            continue

        if stated_days < 30:
            bucket = "< 1 month"
        elif stated_days < 90:
            bucket = "1-3 months"
        elif stated_days < 180:
            bucket = "3-6 months"
        elif stated_days < 365:
            bucket = "6-12 months"
        else:
            bucket = "> 12 months"

        result["by_timeframe"][bucket]["count"] += 1
        result["by_timeframe"][bucket]["total_actual_days"] += actual_days
        result["by_timeframe"][bucket]["total_stated_days"] += stated_days

    for bucket in result["by_timeframe"]:
        data = result["by_timeframe"][bucket]
        if data["count"] > 0:
            data["avg_actual_days"] = round(data["total_actual_days"] / data["count"], 0)
            data["avg_stated_days"] = round(data["total_stated_days"] / data["count"], 0)
            if data["avg_stated_days"] > 0:
                ratio = data["avg_actual_days"] / data["avg_stated_days"]
                if 0.8 <= ratio <= 1.2:
                    data["accuracy"] = "accurate"
                elif ratio < 0.8:
                    data["accuracy"] = "faster than stated"
                else:
                    data["accuracy"] = "slower than stated"
            else:
                data["accuracy"] = "N/A"
        else:
            data["avg_actual_days"] = None
            data["avg_stated_days"] = None
            data["accuracy"] = "N/A"

        del data["total_actual_days"]
        del data["total_stated_days"]

    return result


def analyze_by_theme(history_path: str) -> dict[str, Any]:
    """Analyze win rates grouped by investment theme.

    This is a stub implementation. Full theme analysis would require parsing
    the body of each idea file (not just frontmatter) to extract theme
    associations, or adding a ``theme`` field to the frontmatter schema.

    Parameters:
        history_path: Path to the ``history/ideas/`` directory. Currently
            unused, but accepted for API consistency with other metrics
            functions.

    Returns:
        A dict with:
            - ``themes`` (dict): Empty dict (not yet implemented)
            - ``note`` (str): Explanation that theme analysis is not yet
              implemented
    """
    return {
        "themes": {},
        "note": "Theme analysis requires full file parsing - not yet implemented",
    }


def bootstrap_metrics(history_path: str, output_path: str) -> None:
    """Generate a complete metrics.md file from history data.

    Reads all idea files from the history directory, computes all available
    metrics (win rate, calibration, timeframe accuracy), and writes a
    formatted markdown file suitable for viewing in Obsidian. The output
    includes YAML frontmatter with metadata (update date, tracking period,
    total ideas count).

    The generated file includes:
    - Overall performance table (total ideas, acted, passed, win rate)
    - By-conviction-level breakdown (acted count, wins, win rate per level)
    - Placeholder for theme analysis
    - Calibration analysis table (expected vs actual vs calibrated per level)
    - Timeframe accuracy table (stated vs actual holding periods per bucket)

    All numbers are computed by calling the other functions in this module --
    this function is purely a formatting/output layer.

    Parameters:
        history_path: Path to the ``history/ideas/`` directory containing
            the archived idea markdown files to analyse.
        output_path: File path where the generated ``metrics.md`` will be
            written. Overwrites any existing file at this path.

    Side effects:
        - Reads idea files from disk (via the various ``calculate_*``
          functions).
        - Writes one file to disk at ``output_path``.
    """
    win_rate = calculate_win_rate(history_path)
    calibration = calculate_calibration(history_path)
    timeframe = calculate_timeframe_accuracy(history_path)

    ideas = load_history_ideas(history_path)
    total = len(ideas)
    acted_count = len([t for t in ideas if t["status"] == "acted"])
    passed_count = len([t for t in ideas if t["status"] == "passed"])

    dates: list[date] = []
    for t in ideas:
        try:
            dates.append(datetime.strptime(t["created"], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass
    tracking_since = min(dates).isoformat() if dates else date.today().isoformat()

    def _fmt_rate(rate: float | None) -> str:
        return f"{rate}%" if rate else "-"

    def _fmt_cal(cal_val: bool | None) -> str:
        return str(cal_val) if cal_val is not None else "-"

    def _fmt_tf(bucket: str) -> str:
        d = timeframe["by_timeframe"][bucket]
        avg = d["avg_actual_days"] or "-"
        return f"| {bucket} | {d['count']} | {avg} days | {d['accuracy']} |"

    wr = win_rate["by_conviction"]
    hi_r = _fmt_rate(wr["high"]["rate"])
    md_r = _fmt_rate(wr["medium"]["rate"])
    lo_r = _fmt_rate(wr["low"]["rate"])

    wr_total = _fmt_rate(win_rate["win_rate"])

    cal = calibration
    hi_a = _fmt_rate(cal["high"]["actual"])
    md_a = _fmt_rate(cal["medium"]["actual"])
    lo_a = _fmt_rate(cal["low"]["actual"])
    hi_c = _fmt_cal(cal["high"]["calibrated"])
    md_c = _fmt_cal(cal["medium"]["calibrated"])
    lo_c = _fmt_cal(cal["low"]["calibrated"])

    lines = [
        "---",
        f"updated: {date.today().isoformat()}",
        f"tracking_since: {tracking_since}",
        f"total_ideas: {total}",
        "---",
        "",
        "# Decision Metrics",
        "",
        "All metrics computed from `history/ideas/` files via "
        "`utils/metrics.py`. Never LLM-generated.",
        "",
        "## Overall Performance",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total ideas | {total} |",
        f"| Acted | {acted_count} |",
        f"| Passed | {passed_count} |",
        f"| Win rate (acted) | {wr_total} |",
        "| Pass accuracy | - |",
        "",
        "## By Conviction Level",
        "",
        "| Conviction | Acted | Wins | Win Rate |",
        "|------------|-------|------|----------|",
        f"| High | {wr['high']['acted']} | {wr['high']['wins']} | {hi_r} |",
        f"| Medium | {wr['medium']['acted']} | {wr['medium']['wins']} | {md_r} |",
        f"| Low | {wr['low']['acted']} | {wr['low']['wins']} | {lo_r} |",
        "",
        "## By Theme",
        "",
        "[Theme-specific win rates will appear here as ideas are tracked]",
        "",
        "## Calibration Analysis",
        "",
        "Compares stated conviction vs actual outcomes.",
        "",
        "| Conviction | Expected Win Rate | Actual Win Rate | Calibration |",
        "|------------|-------------------|-----------------|-------------|",
        f"| High | {cal['high']['expected']} | {hi_a} | {hi_c} |",
        f"| Medium | {cal['medium']['expected']} | {md_a} | {md_c} |",
        f"| Low | {cal['low']['expected']} | {lo_a} | {lo_c} |",
        "",
        f"Overall: {cal['overall_calibration']}",
        "",
        "## Timeframe Accuracy",
        "",
        "Compares stated timeframe vs actual holding period.",
        "",
        "| Timeframe | Count | Avg Actual | Accuracy |",
        "|-----------|-------|------------|----------|",
        _fmt_tf("< 1 month"),
        _fmt_tf("1-3 months"),
        _fmt_tf("3-6 months"),
        _fmt_tf("6-12 months"),
        _fmt_tf("> 12 months"),
    ]

    content = "\n".join(lines) + "\n"

    with open(output_path, "w") as f:
        f.write(content)
