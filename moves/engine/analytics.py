"""Portfolio analytics engine for computing performance metrics.

All methods now accept user_id for multi-user scoping.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from typing import Any

from db.database import Database

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    """Computes portfolio performance metrics from database history."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def sharpe_ratio(self, user_id: int, days: int = 252, rf: float = 0.045) -> float:
        """Calculate annualized Sharpe ratio from daily NAV returns.

        Args:
            user_id: ID of the owning user.
            days: Number of trading days to look back.
            rf: Annual risk-free rate.

        Returns:
            Annualized Sharpe ratio. Returns 0.0 if insufficient data.
        """
        returns = self._daily_returns(user_id, days)
        if len(returns) < 2:
            return 0.0

        daily_rf = rf / 252
        excess = [r - daily_rf for r in returns]
        mean_excess = sum(excess) / len(excess)
        variance = sum((r - mean_excess) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0:
            return 0.0
        return (mean_excess / std) * math.sqrt(252)

    def benchmark_comparison(self, user_id: int, benchmark: str = "SPY") -> dict[str, float]:
        """Compare portfolio returns against a benchmark.

        Args:
            user_id: ID of the owning user.
            benchmark: Benchmark ticker symbol.

        Returns:
            Dict with alpha, beta, correlation.
        """
        port_returns = self._daily_returns(user_id, 252)
        bench_returns = self._benchmark_returns(benchmark, len(port_returns))

        n = min(len(port_returns), len(bench_returns))
        if n < 2:
            return {"alpha": 0.0, "beta": 0.0, "correlation": 0.0}

        pr = port_returns[-n:]
        br = bench_returns[-n:]

        mean_p = sum(pr) / n
        mean_b = sum(br) / n

        cov = sum((pr[i] - mean_p) * (br[i] - mean_b) for i in range(n)) / (n - 1)
        var_b = sum((b - mean_b) ** 2 for b in br) / (n - 1)
        var_p = sum((p - mean_p) ** 2 for p in pr) / (n - 1)

        beta = cov / var_b if var_b > 0 else 0.0
        alpha = (mean_p - beta * mean_b) * 252

        denom = math.sqrt(var_p * var_b) if var_p > 0 and var_b > 0 else 0.0
        correlation = cov / denom if denom > 0 else 0.0

        return {"alpha": alpha, "beta": beta, "correlation": correlation}

    def win_rate(self, user_id: int, group_by: str = "all") -> dict[str, Any]:
        """Calculate win rates from closed trades.

        Args:
            user_id: ID of the owning user.
            group_by: Grouping method.

        Returns:
            Dict with win_rate, total_trades, wins, losses.
        """
        trades = self.db.fetchall(
            "SELECT t.realized_pnl, t.signal_id, s.confidence, s.source, s.thesis_id "
            "FROM trades t LEFT JOIN signals s ON t.signal_id = s.id "
            "WHERE t.realized_pnl IS NOT NULL AND t.user_id = ?",
            (user_id,),
        )

        if not trades:
            return {"win_rate": 0.0, "total_trades": 0, "wins": 0, "losses": 0}

        if group_by == "all":
            wins = sum(1 for t in trades if t["realized_pnl"] > 0)
            return {
                "win_rate": wins / len(trades),
                "total_trades": len(trades),
                "wins": wins,
                "losses": len(trades) - wins,
            }

        groups: dict[str, list[dict]] = {}
        for t in trades:
            key = str(t.get(self._group_key(group_by), "unknown"))
            groups.setdefault(key, []).append(t)

        result: dict[str, Any] = {}
        for key, group_trades in groups.items():
            wins = sum(1 for t in group_trades if t["realized_pnl"] > 0)
            result[key] = {
                "win_rate": wins / len(group_trades),
                "total_trades": len(group_trades),
                "wins": wins,
                "losses": len(group_trades) - wins,
            }
        return result

    def calibration(self, user_id: int) -> list[dict[str, Any]]:
        """Analyze confidence calibration.

        Args:
            user_id: ID of the owning user.

        Returns:
            List of dicts with bucket, predicted, actual, count.
        """
        trades = self.db.fetchall(
            "SELECT s.confidence, t.realized_pnl "
            "FROM trades t JOIN signals s ON t.signal_id = s.id "
            "WHERE t.realized_pnl IS NOT NULL AND s.confidence IS NOT NULL AND t.user_id = ?",
            (user_id,),
        )

        buckets: dict[str, list[dict]] = {}
        for t in trades:
            conf = t["confidence"]
            bucket_val = int(conf * 10) / 10
            bucket_label = f"{bucket_val:.0%}-{bucket_val + 0.1:.0%}"
            buckets.setdefault(bucket_label, []).append(t)

        result = []
        for label, bucket_trades in sorted(buckets.items()):
            wins = sum(1 for t in bucket_trades if t["realized_pnl"] > 0)
            confs = [t["confidence"] for t in bucket_trades]
            result.append(
                {
                    "bucket": label,
                    "predicted": sum(confs) / len(confs),
                    "actual": wins / len(bucket_trades),
                    "count": len(bucket_trades),
                }
            )
        return result

    def max_drawdown(self, user_id: int) -> dict[str, Any]:
        """Calculate maximum and current drawdown from NAV history.

        Args:
            user_id: ID of the owning user.

        Returns:
            Dict with max_dd, current_dd, days_underwater, peak_date, trough_date.
        """
        navs = self.db.fetchall(
            "SELECT date, total_value FROM portfolio_value WHERE user_id = ? ORDER BY date",
            (user_id,),
        )

        if not navs:
            return {
                "max_dd": 0.0,
                "current_dd": 0.0,
                "days_underwater": 0,
                "peak_date": None,
                "trough_date": None,
            }

        peak = navs[0]["total_value"]
        peak_date = navs[0]["date"]
        max_dd = 0.0
        max_dd_peak_date = peak_date
        max_dd_trough_date = peak_date
        current_dd = 0.0
        days_underwater = 0

        for nav in navs:
            val = nav["total_value"]
            if val >= peak:
                peak = val
                peak_date = nav["date"]
                days_underwater = 0
            else:
                dd = (peak - val) / peak
                current_dd = dd
                days_underwater += 1
                if dd > max_dd:
                    max_dd = dd
                    max_dd_peak_date = peak_date
                    max_dd_trough_date = nav["date"]

        return {
            "max_dd": max_dd,
            "current_dd": current_dd,
            "days_underwater": days_underwater,
            "peak_date": max_dd_peak_date,
            "trough_date": max_dd_trough_date,
        }

    def var_95(self, user_id: int) -> float:
        """Calculate parametric 95% Value-at-Risk.

        Args:
            user_id: ID of the owning user.

        Returns:
            VaR as a positive fraction.
        """
        returns = self._daily_returns(user_id, 252)
        if len(returns) < 2:
            return 0.0

        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)

        var = -(mean - 1.645 * std)
        return max(var, 0.0)

    def stress_test(self, user_id: int, market_drop: float = -0.20) -> dict[str, Any]:
        """Estimate portfolio impact from a market stress scenario.

        Args:
            user_id: ID of the owning user.
            market_drop: Assumed market decline as a negative fraction.

        Returns:
            Dict with scenario, estimated_loss, estimated_nav, current_nav.
        """
        nav_row = self.db.fetchone(
            "SELECT total_value FROM portfolio_value WHERE user_id = ? ORDER BY date DESC LIMIT 1",
            (user_id,),
        )
        current_nav = nav_row["total_value"] if nav_row else 0.0

        bench = self.benchmark_comparison(user_id)
        beta = bench.get("beta", 1.0) or 1.0

        estimated_loss_pct = market_drop * beta
        estimated_loss = current_nav * estimated_loss_pct
        estimated_nav = current_nav + estimated_loss

        return {
            "scenario": f"Market {market_drop:.0%}",
            "estimated_loss_pct": estimated_loss_pct,
            "estimated_loss": estimated_loss,
            "estimated_nav": estimated_nav,
            "current_nav": current_nav,
        }

    def correlation_matrix(self, user_id: int) -> dict[str, Any]:
        """Compute return correlations between theses.

        Args:
            user_id: ID of the owning user.

        Returns:
            Dict mapping thesis pairs to correlation values.
        """
        trades = self.db.fetchall(
            "SELECT s.thesis_id, t.realized_pnl, t.total_value "
            "FROM trades t JOIN signals s ON t.signal_id = s.id "
            "WHERE s.thesis_id IS NOT NULL AND t.realized_pnl IS NOT NULL AND t.user_id = ?",
            (user_id,),
        )

        thesis_returns: dict[int, list[float]] = {}
        for t in trades:
            tid = t["thesis_id"]
            ret = t["realized_pnl"] / t["total_value"] if t["total_value"] else 0.0
            thesis_returns.setdefault(tid, []).append(ret)

        theses = sorted(thesis_returns.keys())
        result: dict[str, float] = {}
        for i, t1 in enumerate(theses):
            for t2 in theses[i + 1 :]:
                r1, r2 = thesis_returns[t1], thesis_returns[t2]
                n = min(len(r1), len(r2))
                if n < 2:
                    continue
                corr = self._correlation(r1[:n], r2[:n])
                result[f"{t1}-{t2}"] = corr

        return result

    def nav_history(self, user_id: int, days: int = 365) -> list[dict[str, Any]]:
        """Retrieve NAV history.

        Args:
            user_id: ID of the owning user.
            days: Number of days to look back.

        Returns:
            List of dicts with date and nav keys.
        """
        rows = self.db.fetchall(
            "SELECT date, total_value FROM portfolio_value "
            "WHERE user_id = ? AND date >= date('now', ? || ' days') ORDER BY date",
            (user_id, f"-{days}"),
        )
        return [{"date": r["date"], "nav": r["total_value"]} for r in rows]

    def snapshot_nav(self, user_id: int) -> None:
        """Record current NAV to portfolio_value table.

        Args:
            user_id: ID of the owning user.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        latest = self.db.fetchone(
            "SELECT cash, cost_basis FROM portfolio_value WHERE user_id = ? ORDER BY date DESC LIMIT 1",
            (user_id,),
        )
        cash = latest["cash"] if latest else 0.0
        cost_basis = latest["cost_basis"] if latest else 0.0

        positions = self.db.fetchall(
            "SELECT p.symbol, p.shares, p.avg_cost, "
            "COALESCE((SELECT ph.close FROM price_history ph "
            "WHERE ph.symbol = p.symbol ORDER BY ph.timestamp DESC LIMIT 1), p.avg_cost) as price "
            "FROM positions p WHERE p.shares > 0 AND p.user_id = ?",
            (user_id,),
        )

        long_value = sum(p["shares"] * p["price"] for p in positions if p["shares"] > 0)
        total_value = cash + long_value

        self.db.execute(
            "INSERT INTO portfolio_value (date, total_value, long_value, cash, cost_basis, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (today, total_value, long_value, cash, cost_basis, user_id),
        )
        self.db.connect().commit()
        logger.info("NAV snapshot: %s = %.2f", today, total_value)

    def snapshot_exposure(self, user_id: int) -> None:
        """Record current exposure breakdown to exposure_snapshots table.

        Args:
            user_id: ID of the owning user.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        nav_row = self.db.fetchone(
            "SELECT total_value FROM portfolio_value WHERE user_id = ? ORDER BY date DESC LIMIT 1",
            (user_id,),
        )
        nav = nav_row["total_value"] if nav_row else 0.0

        positions = self.db.fetchall(
            "SELECT p.symbol, p.shares, p.side, p.thesis_id, "
            "COALESCE((SELECT ph.close FROM price_history ph "
            "WHERE ph.symbol = p.symbol ORDER BY ph.timestamp DESC LIMIT 1), p.avg_cost) as price "
            "FROM positions p WHERE p.shares > 0 AND p.user_id = ?",
            (user_id,),
        )

        long_val = sum(p["shares"] * p["price"] for p in positions if p["side"] == "long")
        short_val = sum(p["shares"] * p["price"] for p in positions if p["side"] == "short")
        gross = long_val + short_val
        net = long_val - short_val

        long_pct = long_val / nav if nav > 0 else 0.0
        short_pct = short_val / nav if nav > 0 else 0.0
        gross_exp = gross / nav if nav > 0 else 0.0
        net_exp = net / nav if nav > 0 else 0.0

        by_thesis: dict[str, float] = {}
        for p in positions:
            tid = str(p.get("thesis_id", "none"))
            val = p["shares"] * p["price"]
            by_thesis[tid] = by_thesis.get(tid, 0.0) + val

        self.db.execute(
            "INSERT INTO exposure_snapshots "
            "(date, gross_exposure, net_exposure, long_pct, short_pct, by_thesis, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (today, gross_exp, net_exp, long_pct, short_pct, json.dumps(by_thesis), user_id),
        )
        self.db.connect().commit()
        logger.info("Exposure snapshot: gross=%.2f net=%.2f", gross_exp, net_exp)

    # --- Private helpers ---

    def _daily_returns(self, user_id: int, days: int) -> list[float]:
        """Extract daily return percentages from portfolio_value table."""
        rows = self.db.fetchall(
            "SELECT total_value FROM portfolio_value "
            "WHERE user_id = ? AND date >= date('now', ? || ' days') ORDER BY date",
            (user_id, f"-{days}"),
        )
        if len(rows) < 2:
            return []

        returns = []
        for i in range(1, len(rows)):
            prev = rows[i - 1]["total_value"]
            curr = rows[i]["total_value"]
            if prev > 0:
                returns.append((curr - prev) / prev)
        return returns

    def _benchmark_returns(self, symbol: str, count: int) -> list[float]:
        """Fetch benchmark daily returns from price_history."""
        rows = self.db.fetchall(
            "SELECT close FROM price_history WHERE symbol = ? "
            "AND interval = '1d' ORDER BY timestamp LIMIT ?",
            (symbol, count + 1),
        )
        if len(rows) < 2:
            return []

        returns = []
        for i in range(1, len(rows)):
            prev = rows[i - 1]["close"]
            curr = rows[i]["close"]
            if prev > 0:
                returns.append((curr - prev) / prev)
        return returns

    @staticmethod
    def _group_key(group_by: str) -> str:
        """Map group_by parameter to trade/signal field name."""
        mapping = {
            "conviction": "confidence",
            "source": "source",
            "thesis": "thesis_id",
            "domain": "thesis_id",
        }
        return mapping.get(group_by, "source")

    @staticmethod
    def _correlation(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation between two series."""
        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / (n - 1)
        var_x = sum((v - mean_x) ** 2 for v in x) / (n - 1)
        var_y = sum((v - mean_y) ** 2 for v in y) / (n - 1)

        denom = math.sqrt(var_x * var_y)
        return cov / denom if denom > 0 else 0.0
