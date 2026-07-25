import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from app.db import init_db


@pytest.fixture
def test_engine(tmp_path, monkeypatch):
    """A fresh, file-based SQLite engine per test, wired into every module
    that did `from app.db import engine` (a plain import binds the name at
    import time, so patching app.db.engine alone would not affect them)."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    monkeypatch.setattr("app.db.DB_PATH", str(db_path))
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr("app.main.engine", engine)
    monkeypatch.setattr("app.scheduler.engine", engine)
    monkeypatch.setattr("app.auth.engine", engine)
    # aircraft_db imports DB_PATH by value (not the engine), so it needs its
    # own patch target to see the per-test database file.
    monkeypatch.setattr("app.aircraft_db.DB_PATH", str(db_path))

    init_db()
    return engine


@pytest.fixture
def client(test_engine, monkeypatch):
    """A TestClient over the real app, but WITHOUT running the startup event
    (that would start the scheduler - background threads + real OpenSky
    requests - which tests neither need nor want). The DB is already
    initialized by the test_engine fixture above.

    reschedule_poll_job talks to the live APScheduler job store, which only
    exists once start_scheduler() has run - stubbed out since that's a
    separate concern from what these route tests check.
    """
    from app.main import app

    monkeypatch.setattr("app.main.reschedule_poll_job", lambda seconds: None)
    return TestClient(app)


def register(client: TestClient, email: str, password: str = "testpassword1"):
    return client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
        follow_redirects=False,
    )
