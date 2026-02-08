"""Tests for the Scheduler class."""

from __future__ import annotations

from unittest.mock import MagicMock

from db.database import Database
from engine.scheduler import MAX_RETRIES, Scheduler


class TestSchedulerRegistration:
    """Test job registration and management."""

    def test_register_default_jobs(self, seeded_db: Database) -> None:
        """All 10 default jobs should be registered."""
        sched = Scheduler(seeded_db)
        sched.register_default_jobs()

        jobs = sched.get_jobs()
        names = {j["task_name"] for j in jobs}
        expected = {
            "price_update",
            "news_scan",
            "pre_market_review",
            "nav_snapshot",
            "congress_trades",
            "stale_thesis_check",
            "exposure_snapshot",
            "whatif_update",
            "signal_expiry",
            "principle_validation",
        }
        assert names == expected

    def test_add_and_remove_job(self, seeded_db: Database) -> None:
        """Adding and removing a job should update the registry."""
        from apscheduler.triggers.interval import IntervalTrigger

        sched = Scheduler(seeded_db)
        sched.add_job("test_job", lambda: None, IntervalTrigger(seconds=60))

        assert any(j["task_name"] == "test_job" for j in sched.get_jobs())

        sched.remove_job("test_job")
        assert "test_job" not in sched._jobs

    def test_get_jobs_empty(self, db: Database) -> None:
        """get_jobs on empty db returns empty list."""
        sched = Scheduler(db)
        assert sched.get_jobs() == []


class TestSchedulerStartStop:
    """Test scheduler lifecycle."""

    def test_start_stop(self, seeded_db: Database) -> None:
        """Scheduler should start and stop cleanly."""
        sched = Scheduler(seeded_db)
        sched.start()
        assert sched._scheduler.running
        sched.stop()
        assert not sched._scheduler.running

    def test_stop_when_not_running(self, seeded_db: Database) -> None:
        """Stopping an already-stopped scheduler should not error."""
        sched = Scheduler(seeded_db)
        sched.stop()  # no error


class TestSchedulerRetry:
    """Test error handling and retry logic."""

    def test_successful_execution(self, seeded_db: Database) -> None:
        """Successful job should update status to active."""
        sched = Scheduler(seeded_db)
        func = MagicMock()

        sched._ensure_task_row("test_task", "every 1h")
        sched._execute_with_retry("test_task", func)

        func.assert_called_once()
        row = seeded_db.fetchone("SELECT status FROM scheduled_tasks WHERE name = 'test_task'")
        assert row["status"] == "active"

    def test_retry_on_failure(self, seeded_db: Database) -> None:
        """Job that fails should be retried MAX_RETRIES times."""
        sched = Scheduler(seeded_db)
        func = MagicMock(side_effect=RuntimeError("boom"))

        sched._ensure_task_row("fail_task", "every 1h")
        sched._execute_with_retry("fail_task", func)

        assert func.call_count == MAX_RETRIES
        row = seeded_db.fetchone(
            "SELECT status, error_log FROM scheduled_tasks WHERE name = 'fail_task'"
        )
        assert row["status"] == "failed"
        assert "boom" in row["error_log"]

    def test_retry_succeeds_on_second_attempt(self, seeded_db: Database) -> None:
        """Job that fails once then succeeds should end as active."""
        sched = Scheduler(seeded_db)
        func = MagicMock(side_effect=[RuntimeError("fail"), None])

        sched._ensure_task_row("retry_task", "every 1h")
        sched._execute_with_retry("retry_task", func)

        assert func.call_count == 2
        row = seeded_db.fetchone("SELECT status FROM scheduled_tasks WHERE name = 'retry_task'")
        assert row["status"] == "active"

    def test_logs_last_run(self, seeded_db: Database) -> None:
        """Executing a job should set last_run timestamp."""
        sched = Scheduler(seeded_db)
        sched._ensure_task_row("timed_task", "every 1h")
        sched._execute_with_retry("timed_task", lambda: None)

        row = seeded_db.fetchone("SELECT last_run FROM scheduled_tasks WHERE name = 'timed_task'")
        assert row["last_run"] is not None
