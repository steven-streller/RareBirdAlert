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


def test_existing_db_without_event_type_column_gets_migrated_and_defaults_to_landing(tmp_path, monkeypatch):
    """Simulates an instance that predates the approach/departure events: a
    `sighting` table without event_type, with rows already in it. Existing
    rows are all landings by definition, so they must backfill to 'landing'
    rather than an empty/null value."""
    db_path = tmp_path / "legacy_event_type.db"
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
            landed_at TEXT,
            route_origin_icao TEXT,
            route_origin_name TEXT,
            route_destination_icao TEXT,
            route_destination_name TEXT,
            photo_thumbnail_url TEXT,
            photo_large_url TEXT,
            photo_link TEXT
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

    assert sighting.event_type == "landing"


def test_existing_db_without_track_state_notified_columns_gets_migrated(tmp_path, monkeypatch):
    """Simulates an instance that predates the approach/takeoff_roll one-shot
    flags: an `aircrafttrackstate` table without them, with rows already in
    it. init_db() must add them without touching existing data."""
    db_path = tmp_path / "legacy_track_state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE aircrafttrackstate (
            id INTEGER PRIMARY KEY,
            icao24 TEXT,
            airport_id INTEGER,
            on_ground BOOLEAN,
            last_seen_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO aircrafttrackstate (id, icao24, airport_id, on_ground, last_seen_at) "
        "VALUES (1, 'abc123', 1, 1, '2026-01-01')"
    )
    conn.commit()
    conn.close()

    from sqlmodel import create_engine

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()

    from app.models import AircraftTrackState

    with Session(engine) as session:
        track = session.exec(select(AircraftTrackState).where(AircraftTrackState.icao24 == "abc123")).first()

    assert track.on_ground is True
    assert track.approach_notified is False
    assert track.rolling_notified is False
    assert track.last_ground_speed_kt is None


def test_init_db_generates_vapid_keys_once(tmp_path, monkeypatch):
    db_path = tmp_path / "vapid.db"
    from sqlmodel import create_engine

    from app.db import get_setting

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module.init_db()

    with Session(engine) as session:
        private_key_1 = get_setting(session, "vapid_private_key_pem")
        public_key_1 = get_setting(session, "vapid_public_key")
    assert private_key_1.startswith("-----BEGIN PRIVATE KEY-----")
    assert public_key_1

    # a second init_db() run (e.g. container restart) must not regenerate
    # the keypair - every existing browser subscription would otherwise
    # silently stop working, since they're bound to the old public key.
    db_module.init_db()

    with Session(engine) as session:
        private_key_2 = get_setting(session, "vapid_private_key_pem")
        public_key_2 = get_setting(session, "vapid_public_key")
    assert private_key_2 == private_key_1
    assert public_key_2 == public_key_1


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
