"""Tests for database backup system."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from engine.backup import BackupManager


@pytest.fixture
def tmp_setup(tmp_path):
    """Create a temporary DB and backup directory."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t VALUES (1, 'hello')")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    return db_path, backup_dir


class TestCreateBackup:
    """Tests for backup creation."""

    def test_creates_backup_file(self, tmp_setup):
        """Backup file is created with correct data."""
        db_path, backup_dir = tmp_setup
        mgr = BackupManager(str(db_path), str(backup_dir))

        path = mgr.create_backup()

        assert Path(path).exists()
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT val FROM t WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == "hello"

    def test_backup_dir_created(self, tmp_setup):
        """Backup directory is created if it doesn't exist."""
        db_path, backup_dir = tmp_setup
        mgr = BackupManager(str(db_path), str(backup_dir))

        mgr.create_backup()
        assert backup_dir.exists()


class TestCleanupOld:
    """Tests for backup cleanup."""

    def test_removes_old_backups(self, tmp_setup):
        """Backups older than retention period are deleted."""
        db_path, backup_dir = tmp_setup
        mgr = BackupManager(str(db_path), str(backup_dir))

        # Create a "old" backup with old timestamp in name
        backup_dir.mkdir(parents=True, exist_ok=True)
        old_file = backup_dir / "moves_20200101_000000.db"
        old_file.write_text("old")

        deleted = mgr.cleanup_old(keep_days=30)
        assert deleted == 1
        assert not old_file.exists()

    def test_keeps_recent_backups(self, tmp_setup):
        """Recent backups are not deleted."""
        db_path, backup_dir = tmp_setup
        mgr = BackupManager(str(db_path), str(backup_dir))

        # Create a fresh backup
        path = mgr.create_backup()
        deleted = mgr.cleanup_old(keep_days=30)

        assert deleted == 0
        assert Path(path).exists()


class TestDailyBackup:
    """Tests for daily backup routine."""

    def test_daily_creates_and_cleans(self, tmp_setup):
        """Daily backup creates new backup and cleans old ones."""
        db_path, backup_dir = tmp_setup
        mgr = BackupManager(str(db_path), str(backup_dir))

        path = mgr.daily_backup()
        assert Path(path).exists()
