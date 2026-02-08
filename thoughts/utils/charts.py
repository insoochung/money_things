"""Chart generation for the investment journal.

Generates ```chart YAML code blocks compatible with the Obsidian Charts plugin.
Data sourced from SQLite via utils/db.

This module provides functions that query the SQLite database (via ``utils.db``)
for price history and portfolio value data, then format that data into
Obsidian Charts-compatible YAML code blocks. These chart blocks can be
embedded directly in markdown notes and rendered by the Obsidian Charts
plugin as interactive line charts.

The output format looks like::

    ```chart
    type: line
    labels: [2026-01-06, 2026-01-13, 2026-01-20]
    series:
      - title: AAPL
        data: [705.20, 698.50, 712.30]
    ```

Key design decisions:
    - **Inline arrays** -- Obsidian Charts expects labels and data as inline
      YAML arrays (``[a, b, c]``), not block-style lists. The ``_to_chart_yaml``
      helper handles this formatting.
    - **Data sampling** -- For datasets larger than 90 points, the
      ``_sample_points`` helper down-samples to approximately weekly frequency
      to keep charts readable and performant in Obsidian.
    - **Database dependency** -- All chart functions call ``init_db()`` before
      querying to ensure the schema exists. Data must be populated first
      (e.g. via ``/pulse`` or ``backfill_prices``).

Functions:
    - ``price_chart(symbol, period_days)`` -- Single-symbol price line chart
    - ``multi_price_chart(symbols, period_days, normalized)`` -- Multi-symbol
      overlay chart with optional normalisation to % change
    - ``portfolio_value_chart(period_days)`` -- Total portfolio value over time

Internal helpers:
    - ``_to_chart_yaml(chart)`` -- Convert a chart dict to YAML string
    - ``_sample_points(data, key_date, key_value)`` -- Down-sample large datasets
"""

from __future__ import annotations

from datetime import date, timedelta

from utils.db import get_portfolio_value_history, get_price_history, init_db


def _to_chart_yaml(chart: dict) -> str:
    """Convert a chart specification dict to YAML formatted for Obsidian Charts.

    Produces YAML with inline arrays (bracket notation) for labels and data,
    which is the format expected by the Obsidian Charts plugin. Multi-line
    block-style arrays would not render correctly.

    Parameters:
        chart: A dict with the following structure:
            - ``type`` (str): Chart type (e.g. ``"line"``)
            - ``labels`` (list[str]): X-axis labels (typically dates)
            - ``series`` (list[dict]): List of series, each with:
                - ``title`` (str): Series name (e.g. ticker symbol)
                - ``data`` (list[float]): Y-axis values

    Returns:
        A YAML string with trailing newline, ready to be wrapped in
        a ``\\`\\`\\`chart`` code fence. Example::

            type: line
            labels: [2026-01-06, 2026-01-13]
            series:
              - title: AAPL
                data: [705.20, 698.50]
    """
    lines: list[str] = []
    lines.append(f"type: {chart['type']}")
    lines.append(f"labels: [{', '.join(chart['labels'])}]")
    lines.append("series:")
    for s in chart["series"]:
        lines.append(f"  - title: {s['title']}")
        data_str = ", ".join(str(v) for v in s["data"])
        lines.append(f"    data: [{data_str}]")
    return "\n".join(lines) + "\n"


def _sample_points(
    data: list[dict], key_date: str, key_value: str
) -> tuple[list[str], list[float]]:
    """Down-sample a list of data points to keep charts readable.

    For datasets with 90 or fewer points, all points are kept (daily
    granularity). For larger datasets, points are sampled at approximately
    weekly intervals (targeting ~52 samples per year). The last data point
    is always included to ensure the chart extends to the most recent date.

    Date values are truncated to the first 10 characters (``YYYY-MM-DD``
    format) to strip any time component. Numeric values are rounded to
    2 decimal places.

    Parameters:
        data: List of dicts, each representing a data point. Must contain
            at least the keys specified by ``key_date`` and ``key_value``.
        key_date: The dict key for the date/timestamp field (e.g.
            ``"timestamp"`` for price_history rows, ``"date"`` for
            portfolio_value rows).
        key_value: The dict key for the numeric value field (e.g.
            ``"close"`` for prices, ``"total_value"`` for portfolio).

    Returns:
        A tuple of ``(labels, values)`` where:
            - ``labels`` (list[str]): Date strings in ``YYYY-MM-DD`` format
            - ``values`` (list[float]): Corresponding numeric values,
              rounded to 2 decimal places
    """
    if len(data) <= 90:
        labels = [d[key_date][:10] for d in data]
        values = [round(d[key_value], 2) for d in data]
        return labels, values

    step = max(len(data) // 52, 1)
    sampled = data[::step]
    if sampled[-1] != data[-1]:
        sampled.append(data[-1])

    labels = [d[key_date][:10] for d in sampled]
    values = [round(d[key_value], 2) for d in sampled]
    return labels, values


def price_chart(symbol: str, period_days: int = 90) -> str:
    """Generate an Obsidian Charts-compatible price chart for a single symbol.

    Queries the SQLite database for daily price history within the specified
    lookback period and formats it as a ``\\`\\`\\`chart`` code block. If no
    price data exists for the symbol in the given period, returns an empty
    string.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Case-insensitive;
            upper-cased before the database query.
        period_days: Number of calendar days of history to include in the
            chart. Defaults to 90 (approximately 3 months).

    Returns:
        A markdown code block string starting with ``\\`\\`\\`chart`` and
        ending with ``\\`\\`\\```, containing the YAML chart specification.
        Returns an empty string (``""``) if no data is available for the
        given symbol and period.

    Side effects:
        - Calls ``init_db()`` to ensure the database schema exists.
        - Opens and closes a SQLite connection (via ``get_price_history``).
    """
    init_db()
    end = date.today()
    start = end - timedelta(days=period_days)

    data = get_price_history(symbol.upper(), start_date=start, end_date=end)
    if not data:
        return ""

    labels, values = _sample_points(data, "timestamp", "close")

    chart = {
        "type": "line",
        "labels": labels,
        "series": [{"title": symbol.upper(), "data": values}],
    }

    return f"```chart\n{_to_chart_yaml(chart)}```"


def multi_price_chart(symbols: list[str], period_days: int = 90, normalized: bool = False) -> str:
    """Generate an Obsidian Charts-compatible chart with multiple price series.

    Plots multiple stock symbols on the same chart for comparison. Optionally
    normalises all series to percentage change from their starting value,
    which is useful for comparing stocks with very different absolute prices.

    When multiple symbols have different amounts of data, the longest set of
    labels is used as the common x-axis. Shorter series will have fewer data
    points but will still align to the same date labels where they overlap.

    Parameters:
        symbols: List of stock ticker symbols (e.g. ``["AAPL", "MSFT"]``).
        period_days: Number of calendar days of history. Defaults to 90.
        normalized: If ``True``, convert each series from absolute prices to
            percentage change from the first data point. This allows
            meaningful visual comparison between stocks with different price
            levels (e.g. a $180 stock vs a $400 stock). Defaults to ``False``.

    Returns:
        A markdown ``\\`\\`\\`chart`` code block string, or empty string if no
        data is available for any of the given symbols.

    Side effects:
        - Calls ``init_db()`` to ensure the database schema exists.
        - Opens and closes SQLite connections (one per symbol via
          ``get_price_history``).
    """
    init_db()
    end = date.today()
    start = end - timedelta(days=period_days)

    all_series: list[dict] = []
    common_labels: list[str] | None = None

    for symbol in symbols:
        data = get_price_history(symbol.upper(), start_date=start, end_date=end)
        if not data:
            continue

        labels, values = _sample_points(data, "timestamp", "close")

        if normalized and values:
            base = values[0]
            if base != 0:
                values = [round((v - base) / base * 100, 2) for v in values]

        all_series.append({"title": symbol.upper(), "data": values})

        if common_labels is None or len(labels) > len(common_labels):
            common_labels = labels

    if not all_series or common_labels is None:
        return ""

    chart = {
        "type": "line",
        "labels": common_labels,
        "series": all_series,
    }

    return f"```chart\n{_to_chart_yaml(chart)}```"


def portfolio_value_chart(period_days: int = 90) -> str:
    """Generate an Obsidian Charts-compatible chart of portfolio value over time.

    Queries the ``portfolio_value`` table for daily snapshots within the
    specified lookback period and formats them as a line chart. The portfolio
    value snapshots are created by the ``/pulse`` skill and stored via
    ``utils.db.record_portfolio_value``.

    Parameters:
        period_days: Number of calendar days of history. Defaults to 90.

    Returns:
        A markdown ``\\`\\`\\`chart`` code block string showing total portfolio
        value over time, or empty string if no snapshots exist in the period.

    Side effects:
        - Calls ``init_db()`` to ensure the database schema exists.
        - Opens and closes a SQLite connection (via
          ``get_portfolio_value_history``).
    """
    init_db()
    end = date.today()
    start = end - timedelta(days=period_days)

    data = get_portfolio_value_history(start_date=start, end_date=end)
    if not data:
        return ""

    labels, values = _sample_points(data, "date", "total_value")

    chart = {
        "type": "line",
        "labels": labels,
        "series": [{"title": "Portfolio Value", "data": values}],
    }

    return f"```chart\n{_to_chart_yaml(chart)}```"
