"""Automated database backup system.

Creates timestamped SQLite backups using the online backup API, and cleans up
old backups beyond a configurable retention period.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class BackupManager:
    """Automated database backup system.

    Creates compressed timestamped copies of the SQLite database and manages
    retention by cleaning up backups older than the configured threshold.

    Attributes:
        db_path: Path to the source database file.
        backup_dir: Directory for storing backup files.
    """

    def __init__(self, db_path: str, backup_dir: str = "data/backups") -> None:
        """Initialize the backup manager.

        Args:
            db_path: Path to the SQLite database file.
            backup_dir: Directory for storing backups (created if needed).
        """
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> str:
        """Create a timestamped backup of the database.

        Uses SQLite's online backup API for a consistent snapshot,
        even while the database is being written to.

        Returns:
            Path to the created backup file.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_name = f"moves_{timestamp}.db"
        backup_path = self.backup_dir / backup_name

        source = sqlite3.connect(str(self.db_path))
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
            logger.info("Backup created: %s", backup_path)
        finally:
            dest.close()
            source.close()

        return str(backup_path)

    def cleanup_old(self, keep_days: int = 30) -> int:
        """Remove backup files older than the retention period.

        Args:
            keep_days: Number of days to retain backups.

        Returns:
            Number of backup files deleted.
        """
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        deleted = 0

        for f in self.backup_dir.glob("moves_*.db"):
            # Parse timestamp from filename: moves_YYYYMMDD_HHMMSS.db
            try:
                ts_str = f.stem.replace("moves_", "")
                file_dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
            except ValueError:
                continue

            if file_dt < cutoff:
                f.unlink()
                deleted += 1
                logger.info("Deleted old backup: %s", f.name)

        return deleted

    def daily_backup(self) -> str:
        """Run daily backup and cleanup routine.

        Creates a new backup and removes old ones beyond 30-day retention.

        Returns:
            Path to the new backup file.
        """
        path = self.create_backup()
        cleaned = self.cleanup_old()
        if cleaned:
            logger.info("Cleaned up %d old backups", cleaned)
        return path
