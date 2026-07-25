import sqlite3

from sqlmodel import Session, select

from app import db as db_module
from app.models import User


def test_existing_db_without_is_admin_column_gets_migrated_and_backfilled(tmp_path, monkeypatch):
    """Simulates an instance that was already running before is_admin
    existed: a `user` table without that column, with rows already in it.
    init_db() must add the column and promote the earliest account to admin
    instead of leaving every existing user locked out of /admin."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE user (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE,
            password_hash TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO user (id, email, password_hash, created_at) VALUES (1, 'first@example.com', 'x', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO user (id, email, password_hash, created_at) VALUES (2, 'second@example.com', 'x', '2026-01-02')"
    )
    conn.commit()
    conn.close()

    from sqlmodel import create_engine

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()

    with Session(engine) as session:
        first_user = session.exec(select(User).where(User.email == "first@example.com")).first()
        second_user = session.exec(select(User).where(User.email == "second@example.com")).first()

    assert first_user.is_admin is True
    assert second_user.is_admin is False


def test_existing_db_without_route_columns_gets_migrated(tmp_path, monkeypatch):
    """Simulates an instance that predates the adsbdb.com route enrichment:
    a `sighting` table without the route_* columns, with rows already in it.
    init_db() must add them without touching existing data."""
    db_path = tmp_path / "legacy_sighting.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sighting (
            id INTEGER PRIMARY KEY,
            airport_id INTEGER,
            icao24 TEXT,
            callsign TEXT,
            registration TEXT,
            typecode TEXT,
            operator TEXT,
            landed_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO sighting (id, airport_id, icao24, callsign, landed_at) "
        "VALUES (1, 1, 'abc123', 'GAF123', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    from sqlmodel import create_engine

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()

    from app.models import Sighting

    with Session(engine) as session:
        sighting = session.exec(select(Sighting).where(Sighting.icao24 == "abc123")).first()

    assert sighting.callsign == "GAF123"
    assert sighting.route_origin_icao is None
    assert sighting.route_destination_icao is None


def test_init_db_is_a_noop_for_admin_when_an_admin_already_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    from sqlmodel import create_engine

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()

    with Session(engine) as session:
        session.add(User(email="first@example.com", password_hash="x", is_admin=True))
        session.add(User(email="second@example.com", password_hash="x", is_admin=False))
        session.commit()

    # a second init_db() run (e.g. container restart) must not touch admin status
    db_module.init_db()

    with Session(engine) as session:
        second_user = session.exec(select(User).where(User.email == "second@example.com")).first()

    assert second_user.is_admin is False
