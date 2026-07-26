import sqlite3

from sqlmodel import create_engine

from app import backup
from app.db import init_db


def _init_source_db(monkeypatch, tmp_path):
    db_path = tmp_path / "source.db"
    monkeypatch.setattr("app.db.DB_PATH", str(db_path))
    monkeypatch.setattr("app.db.engine", create_engine(f"sqlite:///{db_path}"))
    init_db()
    monkeypatch.setattr(backup, "DB_PATH", str(db_path))
    return db_path


def test_run_backup_creates_a_valid_sqlite_file_with_expected_tables(tmp_path, monkeypatch):
    _init_source_db(monkeypatch, tmp_path)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", str(backup_dir))

    backup_path = backup.run_backup()

    assert backup_path.exists()
    conn = sqlite3.connect(backup_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "user" in tables
    assert "sighting" in tables


def test_run_backup_returns_a_path_inside_the_backup_dir(tmp_path, monkeypatch):
    _init_source_db(monkeypatch, tmp_path)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", str(backup_dir))

    backup_path = backup.run_backup()

    assert backup_path.parent == backup_dir
    assert backup_path.name.startswith("rarebirdalert-")


def test_list_backups_returns_empty_list_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "does-not-exist"))
    assert backup.list_backups() == []


def test_prune_old_backups_keeps_only_the_newest_n(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(backup, "BACKUP_KEEP", 2)

    # Filenames sort chronologically (rarebirdalert-YYYYmmdd-HHMMSS.db).
    for name in [
        "rarebirdalert-20260101-000000.db",
        "rarebirdalert-20260102-000000.db",
        "rarebirdalert-20260103-000000.db",
        "rarebirdalert-20260104-000000.db",
    ]:
        (backup_dir / name).write_text("fake")

    backup._prune_old_backups()

    remaining = {p.name for p in backup.list_backups()}
    assert remaining == {"rarebirdalert-20260103-000000.db", "rarebirdalert-20260104-000000.db"}


def test_prune_old_backups_keeps_everything_when_keep_is_zero(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(backup, "BACKUP_KEEP", 0)

    (backup_dir / "rarebirdalert-20260101-000000.db").write_text("fake")
    (backup_dir / "rarebirdalert-20260102-000000.db").write_text("fake")

    backup._prune_old_backups()

    assert len(backup.list_backups()) == 2


def test_run_backup_prunes_after_writing_a_new_backup(tmp_path, monkeypatch):
    _init_source_db(monkeypatch, tmp_path)
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(backup, "BACKUP_KEEP", 1)
    backup_dir.mkdir()
    (backup_dir / "rarebirdalert-20200101-000000.db").write_text("fake-old-backup")

    backup.run_backup()

    remaining = backup.list_backups()
    assert len(remaining) == 1
    assert remaining[0].name != "rarebirdalert-20200101-000000.db"
