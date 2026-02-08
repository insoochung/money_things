"""Database seeding: import historical data from money_journal into money_moves.

This module populates the money_moves database with initial data imported from the
legacy money_journal system (~/workspace/money_journal/). It handles the one-time
migration of accounts, positions, lots, principles, trading windows, congress trades,
watchlist signals, theses (from research files), price history, and risk limits.

The seed module is a critical part of the money_moves bootstrap process. It takes
the unstructured data from the money_journal (markdown files, SQLite database) and
transforms it into the structured tables used by money_moves.

Seed operations and their data sources:
    - seed_accounts: Hardcoded account data (Schwab, E*Trade, Fidelity)
    - seed_trading_windows: Hardcoded META trading windows for 2026
    - seed_positions: Current positions (META 230 shares, QCOM 129 shares)
    - seed_lots: Tax lots for META (8 lots) and QCOM (3 lots) from portfolio.md
    - seed_principles: Investment principles from memory/principles.md
    - seed_congress_trades: Congressional trades from memory/watchlist.md
    - seed_watchlist_signals: Price triggers as pending manual signals
    - seed_theses: Research files from research/*.md parsed into theses
    - seed_price_history: Historical prices copied from journal.db
    - seed_risk_limits: Default risk limits (position size, exposure, drawdown)
    - seed_kill_switch: Initialize kill switch as inactive

Each seed function is idempotent-ish (uses INSERT OR IGNORE where possible) but
is primarily designed for initial seeding of a fresh database. Running seed_all()
on an already-populated database may create duplicate records for functions that
don't use INSERT OR IGNORE.

The seed module depends on the schema being initialized (db.init_schema()) before
any seed functions are called.

Functions:
    seed_all: Run all seed operations and return counts.
    seed_accounts: Import account definitions.
    seed_trading_windows: Import META trading windows.
    seed_positions: Import current positions.
    seed_lots: Import tax lots.
    seed_principles: Import investment principles.
    seed_congress_trades: Import congressional trades.
    seed_watchlist_signals: Import watchlist triggers as signals.
    seed_theses: Import research files as theses.
    seed_price_history: Copy price history from journal.db.
    seed_risk_limits: Set default risk limits.
    seed_kill_switch: Initialize kill switch.
    _parse_research_frontmatter: Parse YAML-like frontmatter from markdown files.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from db.database import Database

logger = logging.getLogger(__name__)

JOURNAL_ROOT = Path.home() / "workspace" / "money_journal"


def seed_all(db: Database) -> dict[str, int]:
    """Run all seed operations in order and return counts of imported rows.

    Executes each seed function in dependency order (accounts before positions,
    positions before lots, etc.) and collects the count of rows imported by each.

    Args:
        db: Database instance with initialized schema.

    Returns:
        Dictionary mapping seed operation names to the number of rows imported.
        Example: {'accounts': 3, 'trading_windows': 4, 'positions': 2, ...}

    Side effects:
        - Populates multiple database tables with seed data.
        - Logs the final counts.
    """
    counts: dict[str, int] = {}
    counts["accounts"] = seed_accounts(db)
    counts["trading_windows"] = seed_trading_windows(db)
    counts["positions"] = seed_positions(db)
    counts["lots"] = seed_lots(db)
    counts["principles"] = seed_principles(db)
    counts["congress_trades"] = seed_congress_trades(db)
    counts["signals"] = seed_watchlist_signals(db)
    counts["theses"] = seed_theses(db)
    counts["price_history"] = seed_price_history(db)
    counts["risk_limits"] = seed_risk_limits(db)
    counts["kill_switch"] = seed_kill_switch(db)
    logger.info("Seed complete: %s", counts)
    return counts


def seed_accounts(db: Database) -> int:
    """Import brokerage account definitions into the accounts table.

    Creates three accounts:
        1. Individual brokerage (Charles Schwab ...441) - META RSU holding + active trading
        2. Stock plan (E*TRADE/Morgan Stanley ...2264) - QCOM RSU holding
        3. 401(k) (Fidelity) - Index funds

    Uses INSERT OR IGNORE to avoid duplicates on re-run.

    Args:
        db: Database instance for writing account records.

    Returns:
        Number of accounts inserted (always 3).

    Side effects:
        - Inserts rows into the accounts table.
        - Commits the database transaction.
    """
    accounts = [
        {
            "name": "Individual (...441)",
            "broker": "Charles Schwab",
            "account_type": "individual_brokerage",
            "account_hash": "441",
            "purpose": "META RSU holding + active trading",
            "trading_restrictions": "META trading windows",
            "active": True,
        },
        {
            "name": "Stock Plan (...2264)",
            "broker": "E*TRADE (Morgan Stanley)",
            "account_type": "stock_plan",
            "account_hash": "2264",
            "purpose": "QCOM RSU holding",
            "trading_restrictions": None,
            "active": True,
        },
        {
            "name": "Meta 401(k)",
            "broker": "Fidelity",
            "account_type": "401k",
            "account_hash": None,
            "purpose": "Index funds",
            "trading_restrictions": None,
            "active": True,
        },
    ]
    count = 0
    for acct in accounts:
        db.execute(
            """INSERT OR IGNORE INTO accounts
               (name, broker, account_type, account_hash, purpose, trading_restrictions, active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                acct["name"],
                acct["broker"],
                acct["account_type"],
                acct["account_hash"],
                acct["purpose"],
                acct["trading_restrictions"],
                acct["active"],
            ),
        )
        count += 1
    db.connect().commit()
    return count


def seed_trading_windows(db: Database) -> int:
    """Import META trading window schedule into the trading_windows table.

    Creates four quarterly trading windows for META in 2026. These define when
    the user is allowed to trade META stock (insider trading compliance).

    Uses INSERT OR IGNORE to avoid duplicates on re-run.

    Args:
        db: Database instance for writing trading window records.

    Returns:
        Number of windows inserted (always 4).

    Side effects:
        - Inserts rows into the trading_windows table.
        - Commits the database transaction.
    """
    windows = [
        ("META", "2026-01-30", "2026-04-01", "Post-Q4 2025 - CURRENT WINDOW"),
        ("META", "2026-05-01", "2026-07-25", "Post-Q1 2026 - After ~Apr 29 earnings"),
        ("META", "2026-08-01", "2026-10-25", "Post-Q2 2026"),
        ("META", "2026-11-01", "2027-01-25", "Post-Q3 2026"),
    ]
    for w in windows:
        db.execute(
            "INSERT OR IGNORE INTO trading_windows (symbol, opens, closes, notes) VALUES (?,?,?,?)",
            w,
        )
    db.connect().commit()
    return len(windows)


def seed_positions(db: Database) -> int:
    """Import current stock positions into the positions table.

    Creates two positions:
        1. META: 230 shares at $663.02 avg cost (Schwab account)
        2. QCOM: 129 shares at $181.43 avg cost (E*Trade account)

    Requires seed_accounts() to be run first (looks up account IDs).

    Args:
        db: Database instance for writing position records.

    Returns:
        Number of positions inserted (always 2).

    Side effects:
        - Reads account IDs from the accounts table.
        - Inserts rows into the positions table.
        - Commits the database transaction.
    """
    # Get account IDs
    schwab = db.fetchone("SELECT id FROM accounts WHERE account_hash = '441'")
    etrade = db.fetchone("SELECT id FROM accounts WHERE account_hash = '2264'")
    schwab_id = schwab["id"] if schwab else None
    etrade_id = etrade["id"] if etrade else None

    positions = [
        (schwab_id, "META", 230, 663.02, "long", "RSU hold", None),
        (etrade_id, "QCOM", 129, 181.43, "long", "RSU hold", None),
    ]
    for p in positions:
        db.execute(
            """INSERT INTO positions
               (account_id, symbol, shares, avg_cost, side, strategy, thesis_id)
               VALUES (?,?,?,?,?,?,?)""",
            p,
        )
    db.connect().commit()
    return len(positions)


def seed_lots(db: Database) -> int:
    """Import tax lots for META and QCOM positions into the lots table.

    Creates 11 lots total:
        - 8 META lots from RSU vests (2024-11 through 2025-11)
        - 3 QCOM lots from RSU vests (2024-02 and 2024-05)

    Each lot includes cost basis, acquisition date, source ('RSU'), and
    holding period classification ('Short Term' or 'Long Term').

    Requires seed_accounts() and seed_positions() to be run first.

    Args:
        db: Database instance for writing lot records.

    Returns:
        Number of lots inserted (always 11).

    Side effects:
        - Reads position IDs from the positions table.
        - Inserts rows into the lots table.
        - Commits the database transaction.
    """
    meta_pos = db.fetchone("SELECT id, account_id FROM positions WHERE symbol = 'META'")
    qcom_pos = db.fetchone("SELECT id, account_id FROM positions WHERE symbol = 'QCOM'")

    mp = (meta_pos["id"], meta_pos["account_id"])
    meta_lots = [
        (*mp, "META", 67, 38669.72, "2024-11-15", "RSU", "Long Term"),
        (*mp, "META", 39, 28730.13, "2025-02-15", "RSU", "Short Term"),
        (*mp, "META", 39, 25715.04, "2025-05-15", "RSU", "Short Term"),
        (*mp, "META", 3, 1978.08, "2025-05-15", "RSU", "Short Term"),
        (*mp, "META", 40, 31285.20, "2025-08-15", "RSU", "Short Term"),
        (*mp, "META", 3, 2346.39, "2025-08-15", "RSU", "Short Term"),
        (*mp, "META", 36, 21940.56, "2025-11-15", "RSU", "Short Term"),
        (*mp, "META", 3, 1828.38, "2025-11-15", "RSU", "Short Term"),
    ]

    qp = (qcom_pos["id"], qcom_pos["account_id"])
    qcom_lots = [
        (*qp, "QCOM", 46, 6990.16, "2024-02-20", "RSU", "Long Term"),
        (*qp, "QCOM", 76, 15029.76, "2024-05-20", "RSU", "Short Term"),
        (*qp, "QCOM", 7, 1384.32, "2024-05-20", "RSU", "Short Term"),
    ]

    all_lots = meta_lots + qcom_lots
    for lot in all_lots:
        db.execute(
            """INSERT INTO lots (position_id, account_id, symbol, shares, cost_basis,
               acquired_date, source, holding_period)
               VALUES (?,?,?,?,?,?,?,?)""",
            lot,
        )
    db.connect().commit()
    return len(all_lots)


def seed_principles(db: Database) -> int:
    """Import investment principles into the principles table.

    Creates four principles imported from the money_journal:
        1. "Insider experience is high-signal for conviction adjustment" (conviction)
        2. "Culture that's hard to work in often correlates with shareholder returns" (conviction)
        3. "Domain expertise creates durable edge - lean into it" (domain, pre-validated)
        4. "Avoid legacy tech companies with rigid structures" (domain)

    Principle #3 starts with validated_count=2 to reflect historical validation
    from the journal.

    Args:
        db: Database instance for writing principle records.

    Returns:
        Number of principles inserted (always 4).

    Side effects:
        - Inserts rows into the principles table.
        - Commits the database transaction.
    """
    principles = [
        {
            "text": "Insider experience is high-signal for conviction adjustment",
            "category": "conviction",
            "origin": "journal_import",
            "validated_count": 0,
            "weight": 0.05,
        },
        {
            "text": "Culture that's hard to work in often correlates with shareholder returns",
            "category": "conviction",
            "origin": "journal_import",
            "validated_count": 0,
            "weight": 0.05,
        },
        {
            "text": "Domain expertise creates durable edge - lean into it",
            "category": "domain",
            "origin": "journal_import",
            "validated_count": 2,
            "weight": 0.05,
        },
        {
            "text": "Avoid legacy tech companies with rigid structures",
            "category": "domain",
            "origin": "journal_import",
            "validated_count": 0,
            "weight": 0.05,
        },
    ]
    for p in principles:
        db.execute(
            """INSERT INTO principles (text, category, origin, validated_count, weight)
               VALUES (?,?,?,?,?)""",
            (p["text"], p["category"], p["origin"], p["validated_count"], p["weight"]),
        )
    db.connect().commit()
    return len(principles)


def seed_congress_trades(db: Database) -> int:
    """Import congressional trading disclosures into the congress_trades table.

    Creates 7 records of Nancy Pelosi's reported trades from January 2026,
    including purchases of NVDA, GOOGL, PANW, TEM, AMZN, VST, and a sale of AAPL.

    Congressional trades are used as a sentiment signal source in the signal engine.

    Args:
        db: Database instance for writing congress trade records.

    Returns:
        Number of trades inserted (always 7).

    Side effects:
        - Inserts rows into the congress_trades table.
        - Commits the database transaction.
    """
    trades = [
        ("Nancy Pelosi", "NVDA", "BUY", None, "2026-01", None, None),
        ("Nancy Pelosi", "GOOGL", "BUY", None, "2026-01", None, None),
        ("Nancy Pelosi", "PANW", "BUY", None, "2026-01", None, None),
        ("Nancy Pelosi", "AAPL", "SELL", None, "2026-01", None, None),
        ("Nancy Pelosi", "TEM", "BUY", "$50K-$100K", "2026-01", None, None),
        ("Nancy Pelosi", "AMZN", "BUY", "$1M-$5M", "2026-01", None, None),
        ("Nancy Pelosi", "VST", "BUY", "$1M-$5M", "2026-01", None, None),
    ]
    for t in trades:
        db.execute(
            """INSERT INTO congress_trades
               (politician, symbol, action, amount_range, date_filed, date_traded, source_url)
               VALUES (?,?,?,?,?,?,?)""",
            t,
        )
    db.connect().commit()
    return len(trades)


def seed_watchlist_signals(db: Database) -> int:
    """Import watchlist price triggers as pending manual signals.

    Creates 6 price-trigger signals for META and QCOM:
        - META: Sell at $850 (take profit), sell at $550 (stop loss), buy at $600
        - QCOM: Sell at $188 (take profit), sell at $115 (stop loss), buy at $130

    These are seeded as PENDING signals with MANUAL source, ready to be activated
    when their price triggers are hit.

    Args:
        db: Database instance for writing signal records.

    Returns:
        Number of signals inserted (always 6).

    Side effects:
        - Inserts rows into the signals table.
        - Commits the database transaction.
    """
    # Price triggers for held positions
    triggers = [
        ("SELL", "META", 0.7, "manual", "Take profit at $850", "pending"),
        ("SELL", "META", 0.6, "manual", "Stop loss at $550", "pending"),
        ("BUY", "META", 0.5, "manual", "Add more at $600", "pending"),
        ("SELL", "QCOM", 0.7, "manual", "Take profit at $188", "pending"),
        ("SELL", "QCOM", 0.6, "manual", "Stop loss at $115", "pending"),
        ("BUY", "QCOM", 0.5, "manual", "Add more at $130", "pending"),
    ]
    count = 0
    for t in triggers:
        db.execute(
            """INSERT INTO signals (action, symbol, confidence, source, reasoning, status)
               VALUES (?,?,?,?,?,?)""",
            t,
        )
        count += 1
    db.connect().commit()
    return count


def _parse_research_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from research markdown files.

    Extracts key-value pairs from the YAML frontmatter block (delimited by '---')
    at the beginning of a markdown file. Only handles simple key: value pairs,
    not nested structures or lists.

    Args:
        content: Full text content of a markdown file that may have YAML frontmatter.

    Returns:
        Dictionary of frontmatter key-value pairs. Empty dict if no frontmatter found.
        List items (lines starting with '-') are skipped.
    """
    result: dict = {}
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return result
    for line in match.group(1).splitlines():
        if ":" in line and not line.strip().startswith("-"):
            key, val = line.split(":", 1)
            result[key.strip()] = val.strip()
    return result


def seed_theses(db: Database) -> int:
    """Import research files from money_journal as investment theses.

    Reads all markdown files from ~/workspace/money_journal/research/*.md and
    creates thesis records from them. For each file:
        1. Parses YAML frontmatter for the symbol (falls back to filename)
        2. Extracts the title from the first heading
        3. Extracts the executive summary (or first paragraph) as thesis_text
        4. Creates a thesis with strategy='long', status='active', source='money_journal'

    Args:
        db: Database instance for writing thesis records.

    Returns:
        Number of theses imported. Returns 0 if the research directory doesn't exist.

    Side effects:
        - Reads markdown files from the filesystem.
        - Inserts rows into the theses table.
        - Commits the database transaction.
        - Logs a warning if the research directory is not found.
    """
    research_dir = JOURNAL_ROOT / "research"
    if not research_dir.exists():
        logger.warning("Research directory not found: %s", research_dir)
        return 0

    count = 0
    for md_file in sorted(research_dir.glob("*.md")):
        content = md_file.read_text()
        fm = _parse_research_frontmatter(content)
        symbol = fm.get("symbol", md_file.stem.upper())

        # Extract title from first heading
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1) if title_match else f"{symbol} Research"

        # Extract executive summary or first paragraph after headings
        summary = ""
        summary_match = re.search(
            r"##\s+Executive Summary\s*\n+(.*?)(?=\n##|\Z)", content, re.DOTALL
        )
        if summary_match:
            summary = summary_match.group(1).strip()[:2000]
        elif not summary:
            # Take first paragraph
            para_match = re.search(r"\n\n([^#\n].+?)(?:\n\n|\Z)", content, re.DOTALL)
            if para_match:
                summary = para_match.group(1).strip()[:2000]

        db.execute(
            """INSERT INTO theses
               (title, thesis_text, strategy, status, symbols, source_module)
               VALUES (?,?,?,?,?,?)""",
            (title, summary, "long", "active", json.dumps([symbol]), "money_journal"),
        )
        count += 1
    db.connect().commit()
    return count


def seed_price_history(db: Database) -> int:
    """Copy price history from the legacy money_journal database.

    Reads all price_history rows from ~/workspace/money_journal/data/journal.db
    and copies them to the money_moves database. Uses INSERT OR IGNORE to skip
    duplicates.

    Args:
        db: Database instance for writing price history records.

    Returns:
        Number of price history rows copied. Returns 0 if journal.db doesn't exist
        or has no price history.

    Side effects:
        - Opens a separate SQLite connection to journal.db (read-only).
        - Bulk inserts rows into the price_history table.
        - Commits the database transaction.
        - Logs a warning if journal.db is not found.
    """
    journal_db = JOURNAL_ROOT / "data" / "journal.db"
    if not journal_db.exists():
        logger.warning("Journal database not found: %s", journal_db)
        return 0

    src = sqlite3.connect(str(journal_db))
    try:
        rows = src.execute(
            "SELECT symbol, timestamp, interval, open, high, low, close, volume FROM price_history"
        ).fetchall()
    finally:
        src.close()

    if not rows:
        return 0

    db.executemany(
        """INSERT OR IGNORE INTO price_history
           (symbol, timestamp, interval, open, high, low, close, volume)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    db.connect().commit()
    return len(rows)


def seed_risk_limits(db: Database) -> int:
    """Seed default risk limits into the risk_limits table.

    Creates 7 risk limit entries with conservative defaults:
        - max_position_pct: 15% (max single position as % of NAV)
        - max_sector_pct: 35% (max sector concentration)
        - max_gross_exposure: 150% (max total exposure)
        - net_exposure_min: -30% (min net exposure, allows some shorting)
        - net_exposure_max: 130% (max net exposure, allows some leverage)
        - max_drawdown: 20% (max drawdown from peak before halting)
        - daily_loss_limit: 3% (max daily loss before halting)

    Uses INSERT OR IGNORE to avoid duplicates on re-run.

    Args:
        db: Database instance for writing risk limit records.

    Returns:
        Number of limits inserted (always 7).

    Side effects:
        - Inserts rows into the risk_limits table.
        - Commits the database transaction.
    """
    limits = [
        ("max_position_pct", 0.15),
        ("max_sector_pct", 0.35),
        ("max_gross_exposure", 1.50),
        ("net_exposure_min", -0.30),
        ("net_exposure_max", 1.30),
        ("max_drawdown", 0.20),
        ("daily_loss_limit", 0.03),
    ]
    for limit_type, value in limits:
        db.execute(
            "INSERT OR IGNORE INTO risk_limits (limit_type, value) VALUES (?, ?)",
            (limit_type, value),
        )
    db.connect().commit()
    return len(limits)


def seed_kill_switch(db: Database) -> int:
    """Initialize the kill switch as inactive.

    Creates a single kill_switch record with active=FALSE, indicating that
    trading is allowed. The kill switch can be activated later by the
    RiskManager or user action.

    Args:
        db: Database instance for writing the kill switch record.

    Returns:
        Always returns 1 (one record inserted).

    Side effects:
        - Inserts a row into the kill_switch table.
        - Commits the database transaction.
    """
    db.execute(
        "INSERT INTO kill_switch (active, reason) VALUES (?, ?)",
        (False, "Initial state"),
    )
    db.connect().commit()
    return 1
