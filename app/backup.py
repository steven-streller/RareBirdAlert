import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from app.db import DB_PATH

logger = logging.getLogger("rarebirdalert.backup")

BACKUP_DIR = os.environ.get("RAREBIRDALERT_BACKUP_DIR", "/app/data/backups")
BACKUP_KEEP = int(os.environ.get("RAREBIRDALERT_BACKUP_KEEP", "7"))


def list_backups() -> list[Path]:
    """Existing backups, oldest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    return sorted(Path(BACKUP_DIR).glob("rarebirdalert-*.db"))


def _prune_old_backups() -> None:
    if BACKUP_KEEP <= 0:
        return
    backups = list_backups()
    for stale in backups[:-BACKUP_KEEP]:
        try:
            stale.unlink()
        except OSError as exc:
            logger.warning("Failed to remove stale backup %s: %s", stale, exc)


def run_backup() -> Path:
    """Writes a consistent point-in-time snapshot of the live database via
    SQLite's built-in VACUUM INTO - safe to run while the app is serving
    requests and the scheduler is writing (WAL mode already used elsewhere,
    see app/db.py), no pausing needed. Prunes older backups down to
    BACKUP_KEEP afterwards.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(BACKUP_DIR) / f"rarebirdalert-{timestamp}.db"

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("VACUUM INTO ?", (str(backup_path),))
    finally:
        conn.close()

    _prune_old_backups()
    logger.info("Backup written to %s", backup_path)
    return backup_path
