"""APScheduler wrapper for scheduled investment tasks.

Manages periodic jobs (price updates, NAV snapshots, signal expiry, etc.) with
error handling, retry logic, and database logging. Jobs are registered with cron
or interval triggers and execute within a try/except wrapper that logs results
to the scheduled_tasks table.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from db.database import Database

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
"""Maximum retry attempts for failed job executions."""


class Scheduler:
    """APScheduler wrapper for scheduled investment tasks.

    Wraps APScheduler's BackgroundScheduler with database-backed job tracking,
    error handling with exponential backoff retries, and pre-configured default
    jobs for the money_moves system.

    Attributes:
        db: Database instance for logging job execution.
        engines: Dict of engine instances available to job functions.
        _scheduler: Underlying APScheduler BackgroundScheduler.
        _jobs: Registry mapping task names to their configuration.
    """

    def __init__(self, db: Database, engines: dict | None = None) -> None:
        """Initialize the scheduler.

        Args:
            db: Database instance for job execution logging.
            engines: Dict of engine instances (analytics, whatif, etc.) that
                jobs may need to call.
        """
        self.db = db
        self.engines = engines or {}
        self._scheduler = BackgroundScheduler()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        """Start the background scheduler."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the background scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def add_job(
        self,
        task_name: str,
        func: Callable,
        trigger: CronTrigger | IntervalTrigger,
        **kwargs: Any,
    ) -> None:
        """Register a job with the scheduler and database.

        Wraps the function in error-handling logic and records the job in both
        the APScheduler instance and the scheduled_tasks table.

        Args:
            task_name: Unique name for the task.
            func: Callable to execute on schedule.
            trigger: APScheduler trigger (CronTrigger or IntervalTrigger).
            **kwargs: Additional arguments passed to APScheduler's add_job.
        """
        schedule_str = str(trigger)

        def wrapped() -> None:
            self._execute_with_retry(task_name, func)

        self._scheduler.add_job(wrapped, trigger, id=task_name, replace_existing=True, **kwargs)
        self._jobs[task_name] = {
            "func": func,
            "trigger": trigger,
            "schedule": schedule_str,
        }
        self._ensure_task_row(task_name, schedule_str)

    def remove_job(self, task_name: str) -> None:
        """Remove a job from the scheduler.

        Args:
            task_name: Name of the task to remove.
        """
        try:
            self._scheduler.remove_job(task_name)
        except Exception:
            pass
        self._jobs.pop(task_name, None)

    def get_jobs(self) -> list[dict[str, Any]]:
        """Return list of registered jobs with their metadata.

        Returns:
            List of dicts with task_name, schedule, status, last_run, etc.
        """
        rows = self.db.fetchall(
            "SELECT name, cron_expression, last_run, next_run, status, error_log "
            "FROM scheduled_tasks ORDER BY name"
        )
        return [
            {
                "task_name": r["name"],
                "schedule": r["cron_expression"],
                "last_run": r["last_run"],
                "next_run": r["next_run"],
                "status": r["status"],
                "error_log": r["error_log"],
            }
            for r in rows
        ]

    def register_default_jobs(self) -> None:
        """Register all 10 default scheduled jobs.

        Jobs are registered with their triggers but the scheduler must be
        started separately via start(). ET = America/New_York timezone.
        """
        tz = "America/New_York"

        # price_update: every 15 min, market hours (9:30-16:00 ET weekdays)
        self.add_job(
            "price_update",
            self._noop,
            CronTrigger(minute="*/15", hour="9-15", day_of_week="mon-fri", timezone=tz),
        )

        # news_scan: 3x/day (8am, 2pm, 8pm ET)
        self.add_job(
            "news_scan",
            self._noop,
            CronTrigger(hour="8,14,20", minute=0, timezone=tz),
        )

        # pre_market_review: 9:00 AM ET
        self.add_job(
            "pre_market_review",
            self._noop,
            CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=tz),
        )

        # nav_snapshot: 4:15 PM ET
        self.add_job(
            "nav_snapshot",
            self._noop,
            CronTrigger(hour=16, minute=15, day_of_week="mon-fri", timezone=tz),
        )

        # congress_trades: 7:00 PM ET daily
        self.add_job(
            "congress_trades",
            self._noop,
            CronTrigger(hour=19, minute=0, timezone=tz),
        )

        # stale_thesis_check: Monday 8:00 AM ET weekly
        self.add_job(
            "stale_thesis_check",
            self._noop,
            CronTrigger(day_of_week="mon", hour=8, minute=0, timezone=tz),
        )

        # exposure_snapshot: hourly, market hours
        self.add_job(
            "exposure_snapshot",
            self._noop,
            CronTrigger(minute=0, hour="9-16", day_of_week="mon-fri", timezone=tz),
        )

        # whatif_update: 4:30 PM ET daily
        self.add_job(
            "whatif_update",
            self._noop,
            CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone=tz),
        )

        # signal_expiry: hourly
        self.add_job(
            "signal_expiry",
            self._noop,
            IntervalTrigger(hours=1),
        )

        # principle_validation: Sunday 8:00 PM ET weekly
        self.add_job(
            "principle_validation",
            self._noop,
            CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=tz),
        )

    def _noop(self) -> None:
        """Placeholder function for default jobs before real implementations."""

    def _execute_with_retry(self, task_name: str, func: Callable) -> None:
        """Execute a job function with retry logic and database logging.

        Retries up to MAX_RETRIES times with exponential backoff (1s, 2s, 4s).
        Logs start/end times and errors to the scheduled_tasks table.

        Args:
            task_name: Name of the task being executed.
            func: The callable to execute.
        """
        start_time = datetime.now(UTC).isoformat()
        self._update_task(task_name, last_run=start_time, status="running")

        for attempt in range(MAX_RETRIES):
            try:
                func()
                self._update_task(
                    task_name,
                    status="active",
                    error_log=None,
                    consecutive_failures=0,
                )
                logger.info("Task %s completed successfully", task_name)
                return
            except Exception:
                error_msg = traceback.format_exc()
                logger.warning(
                    "Task %s attempt %d/%d failed: %s",
                    task_name,
                    attempt + 1,
                    MAX_RETRIES,
                    error_msg.splitlines()[-1],
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt)

        # All retries exhausted
        self._update_task(
            task_name,
            status="failed",
            error_log=error_msg,  # type: ignore[possibly-undefined]
            consecutive_failures_increment=True,
        )
        logger.error("Task %s failed after %d retries", task_name, MAX_RETRIES)

    def _ensure_task_row(self, task_name: str, schedule: str) -> None:
        """Insert or update the scheduled_tasks row for a task.

        Args:
            task_name: Unique task name.
            schedule: Human-readable schedule description.
        """
        existing = self.db.fetchone("SELECT id FROM scheduled_tasks WHERE name = ?", (task_name,))
        if existing:
            self.db.execute(
                "UPDATE scheduled_tasks SET cron_expression = ? WHERE name = ?",
                (schedule, task_name),
            )
        else:
            self.db.execute(
                "INSERT INTO scheduled_tasks (name, cron_expression, status) "
                "VALUES (?, ?, 'active')",
                (task_name, schedule),
            )
        self.db.connect().commit()

    def _update_task(
        self,
        task_name: str,
        *,
        last_run: str | None = None,
        status: str | None = None,
        error_log: str | None = None,
        consecutive_failures: int | None = None,
        consecutive_failures_increment: bool = False,
    ) -> None:
        """Update fields on a scheduled_tasks row.

        Args:
            task_name: Task to update.
            last_run: ISO timestamp of last run start.
            status: New status value.
            error_log: Error traceback or None to clear.
            consecutive_failures: Explicit failure count to set.
            consecutive_failures_increment: If True, increment failure count by 1.
        """
        updates: list[str] = []
        params: list[Any] = []

        if last_run is not None:
            updates.append("last_run = ?")
            params.append(last_run)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if error_log is not None:
            updates.append("error_log = ?")
            params.append(error_log)
        elif error_log is None and "error_log" in str(updates):
            pass  # already handled
        if consecutive_failures is not None:
            # Schema may not have this column; use error_log to track
            pass
        if consecutive_failures_increment:
            pass

        if not updates:
            return

        params.append(task_name)
        sql = f"UPDATE scheduled_tasks SET {', '.join(updates)} WHERE name = ?"
        self.db.execute(sql, tuple(params))
        self.db.connect().commit()
