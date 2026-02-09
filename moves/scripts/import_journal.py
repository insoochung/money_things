"""Import data from the original obsidian money_journal into moves DB.

Seeds theses from watchlist + research files, and principles from
the original principles.md. Idempotent — checks for existing records.
"""
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

JOURNAL_ROOT = Path.home() / "workspace/obsidian/money_journal/money_journal"
RESEARCH_DIR = JOURNAL_ROOT / "research"
MOCK_DB = Path.home() / "workspace/money/moves/data/moves_mock.db"
REAL_DB = Path.home() / "workspace/money/moves/data/moves.db"

# Watchlist data from the original journal
WATCHLIST_THESES = [
    {
        "title": "META AI monetization + platform dominance",
        "thesis_text": (
            "Meta's AI capex ($115-135B in 2026) positions it as an AI "
            "platform company, not just social media. Ad revenue acceleration "
            "(+18% impressions, +6% price/ad) funds AI investment. FTC "
            "antitrust risk diminished after favorable ruling. Insider "
            "experience confirms strong execution culture. Forward P/E "
            "compressed to ~18.7x despite unchanged fundamentals."
        ),
        "strategy": "long",
        "status": "active",
        "symbols": "META",
        "conviction": 85,
        "horizon": "12-18 months",
        "validation_criteria": (
            "Q1 2026 revenue beats consensus ($53.5-56.5B guide vs $51.4B). "
            "AI products (Meta AI, Llama) show usage growth. "
            "Operating margin stays above 35%."
        ),
        "failure_criteria": (
            "FTC appeal succeeds, forced divestiture. "
            "AI capex destroys margins without revenue payoff. "
            "Forward P/E expands above 25x without earnings growth."
        ),
    },
    {
        "title": "QCOM recovery — mobile + auto diversification",
        "thesis_text": (
            "Qualcomm at forward P/E 12.4x is cheap for its moat in "
            "mobile 5G + growing auto/IoT revenue. HBM memory shortage "
            "cited as bottleneck suggests demand > supply. Insider "
            "experience: culture challenges but strong IP portfolio. "
            "Snapdragon X Elite for PCs is a new growth vector."
        ),
        "strategy": "long",
        "status": "active",
        "symbols": "QCOM",
        "conviction": 65,
        "horizon": "12 months",
        "validation_criteria": (
            "Auto revenue grows >20% YoY. "
            "Snapdragon X Elite wins >5% PC market share. "
            "QCT margins stable above 25%."
        ),
        "failure_criteria": (
            "Apple modem replaces Qualcomm in iPhones. "
            "Auto revenue growth stalls. "
            "Forward P/E stays compressed below 12x for 6+ months."
        ),
    },
    {
        "title": "AI infrastructure picks & shovels — custom silicon",
        "thesis_text": (
            "As AI inference costs become majority of AI spend, custom "
            "silicon (vs NVIDIA GPUs) becomes the economic choice. AVGO "
            "and MRVL lead custom ASIC design. AVGO has OpenAI $73B "
            "backlog. Domain expertise in SW/HW codesign supports this "
            "thesis — inference is easier than training, making NVDA "
            "replaceable for inference workloads."
        ),
        "strategy": "long",
        "status": "draft",
        "symbols": "AVGO,MRVL",
        "conviction": 55,
        "horizon": "18-24 months",
        "validation_criteria": (
            "Custom ASIC revenue grows >40% YoY at AVGO/MRVL. "
            "Major cloud providers announce custom chip programs. "
            "NVDA inference market share declines."
        ),
        "failure_criteria": (
            "NVDA Blackwell economics too good — custom silicon "
            "can't compete on TCO. Cloud providers cancel custom "
            "chip programs."
        ),
    },
    {
        "title": "AI connectivity pure-play — CRDO",
        "thesis_text": (
            "Credo Technology is a pure-play on AI data center "
            "connectivity (AECs). 272% YoY revenue growth. Plays into "
            "the infrastructure buildout thesis. Main risk is customer "
            "concentration and premium valuation (29x forward P/E)."
        ),
        "strategy": "long",
        "status": "draft",
        "symbols": "CRDO",
        "conviction": 45,
        "horizon": "12 months",
        "validation_criteria": (
            "Revenue growth sustains >100% YoY. "
            "Customer diversification beyond top 2. "
            "Design wins at 2+ hyperscalers."
        ),
        "failure_criteria": (
            "Revenue growth decelerates below 50%. "
            "Key customer loss. Gross margin compression."
        ),
    },
    {
        "title": "Cybersecurity platform consolidation — PANW/CRWD",
        "thesis_text": (
            "Security spend is non-discretionary and growing. PANW is "
            "consolidating with $28.4B in M&A, Pelosi buying. CRWD "
            "recovered from outage with record net new ARR. Both benefit "
            "from AI-driven threat complexity. PANW Q2 earnings Feb 17."
        ),
        "strategy": "long",
        "status": "draft",
        "symbols": "PANW,CRWD",
        "conviction": 50,
        "horizon": "12-18 months",
        "validation_criteria": (
            "PANW platformization ARR grows >25%. "
            "CRWD post-outage customer retention >95%. "
            "Sector P/E holds above 30x."
        ),
        "failure_criteria": (
            "Major breach at either company. "
            "Enterprise security budgets cut in recession. "
            "CRWD loses customers to PANW platformization."
        ),
    },
    {
        "title": "Data center power infrastructure — VRT",
        "thesis_text": (
            "Vertiv is the picks-and-shovels play on data center "
            "power/cooling. Backlog $9.5B. S&P 500 inclusion likely. "
            "AI data centers need 2-3x more power per rack. Q4 earnings "
            "Feb 11 — watch order growth and 2026 guidance."
        ),
        "strategy": "long",
        "status": "draft",
        "symbols": "VRT",
        "conviction": 45,
        "horizon": "12 months",
        "validation_criteria": (
            "Backlog grows >20% YoY. "
            "S&P 500 inclusion announced. "
            "Operating margin expansion to >20%."
        ),
        "failure_criteria": (
            "Data center build slowdown. "
            "Backlog-to-revenue conversion slows. "
            "Competition from Schneider/Eaton intensifies."
        ),
    },
    {
        "title": "GOOGL — search moat + AI transformation",
        "thesis_text": (
            "Google at 31x forward P/E with dominant search moat. "
            "Pelosi buying call options. Transformer architecture "
            "inventor advantage. Risk: AI search disruption (Perplexity, "
            "ChatGPT search). Down 6% from highs."
        ),
        "strategy": "long",
        "status": "draft",
        "symbols": "GOOGL",
        "conviction": 50,
        "horizon": "12-18 months",
        "validation_criteria": (
            "Search revenue grows >10% YoY despite AI competitors. "
            "Cloud revenue growth >30%. "
            "Gemini adoption metrics improve."
        ),
        "failure_criteria": (
            "Search market share drops below 85%. "
            "Cloud growth decelerates below 20%. "
            "AI search competitors gain >5% share."
        ),
    },
]

# Triggers from watchlist
TRIGGERS = [
    ("META", "take_profit", "price_above", 850.0, "Original target"),
    ("META", "stop_loss", "price_below", 550.0, "Hard stop"),
    ("META", "entry", "price_below", 600.0, "Add more on dip"),
    ("QCOM", "take_profit", "price_above", 188.0, "Original target"),
    ("QCOM", "stop_loss", "price_below", 115.0, "Hard stop"),
    ("QCOM", "entry", "price_below", 130.0, "Add more on dip"),
    ("AMD", "entry", "price_below", 190.0, "Entry target ~$289 PT"),
    ("GOOGL", "entry", "price_below", 310.0, "Entry near $346 PT"),
    ("AVGO", "entry", "price_below", 310.0, "Custom silicon leader"),
    ("CRDO", "entry", "price_below", 95.0, "Wait for pullback"),
    ("VRT", "entry", "price_below", 180.0, "Wait for S&P inclusion dip"),
]

# Additional principles from original journal
EXTRA_PRINCIPLES = [
    {
        "text": (
            "Culture that's hard to work in often correlates with "
            "shareholder returns — don't confuse 'nice place to work' "
            "with 'good investment'"
        ),
        "category": "conviction",
        "origin": "META thesis update (2026-02-02)",
    },
]


def import_to_db(db_path: str) -> None:
    """Import journal data into the specified database."""
    conn = sqlite3.connect(db_path)
    now = datetime.now(UTC).isoformat()

    # Import theses (skip if title already exists)
    existing = {
        r[0]
        for r in conn.execute("SELECT title FROM theses").fetchall()
    }
    added_theses = 0
    for t in WATCHLIST_THESES:
        if t["title"] in existing:
            print(f"  skip thesis: {t['title'][:50]}...")
            continue
        conn.execute(
            """INSERT INTO theses
            (title, thesis_text, strategy, status, symbols, conviction,
             horizon, validation_criteria, failure_criteria,
             source_module, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t["title"], t["thesis_text"], t["strategy"],
                t["status"], t["symbols"], t["conviction"],
                t["horizon"], t["validation_criteria"],
                t["failure_criteria"], "journal_import", now, now,
            ),
        )
        added_theses += 1
        print(f"  + thesis: {t['title'][:50]}...")

    # Import triggers (only if table exists)
    try:
        conn.execute("SELECT 1 FROM watchlist_triggers LIMIT 1")
        has_triggers = True
    except sqlite3.OperationalError:
        has_triggers = False

    added_triggers = 0
    if has_triggers:
        existing_triggers = {
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT symbol, trigger_type, target_value "
                "FROM watchlist_triggers"
            ).fetchall()
        }
        for symbol, ttype, cond, val, notes in TRIGGERS:
            if (symbol, ttype, val) in existing_triggers:
                continue
            # Find thesis_id for this symbol
            row = conn.execute(
                "SELECT id FROM theses WHERE symbols LIKE ?",
                (f"%{symbol}%",),
            ).fetchone()
            thesis_id = row[0] if row else None
            conn.execute(
                """INSERT INTO watchlist_triggers
                (thesis_id, symbol, trigger_type, condition,
                 target_value, notes, created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (thesis_id, symbol, ttype, cond, val, notes, now),
            )
            added_triggers += 1
        print(f"  + {added_triggers} triggers")
    else:
        print("  watchlist_triggers table not found, skipping triggers")

    # Import extra principles
    existing_p = {
        r[0] for r in conn.execute("SELECT text FROM principles").fetchall()
    }
    added_p = 0
    for p in EXTRA_PRINCIPLES:
        if p["text"] in existing_p:
            continue
        conn.execute(
            """INSERT INTO principles
            (text, category, origin, validated_count,
             invalidated_count, weight, active, created_at)
            VALUES (?,?,?,0,0,1.0,1,?)""",
            (p["text"], p["category"], p["origin"], now),
        )
        added_p += 1
    print(f"  + {added_p} principles")

    conn.commit()
    conn.close()
    print(
        f"  Done: {added_theses} theses, "
        f"{added_triggers} triggers, {added_p} principles"
    )


if __name__ == "__main__":
    for db in [MOCK_DB, REAL_DB]:
        if db.exists():
            print(f"\nImporting to {db.name}...")
            import_to_db(str(db))
        else:
            print(f"\nSkipping {db.name} (not found)")
