import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from app.db import init_db

# Captured once, before any monkeypatching, so tests that need to submit a
# request without (or with a deliberately wrong) csrf_token - i.e. the CSRF
# tests themselves - can bypass the autouse auto-injection below via raw_post.
_ORIGINAL_TESTCLIENT_POST = TestClient.post


def raw_post(client: TestClient, url: str, **kwargs):
    return _ORIGINAL_TESTCLIENT_POST(client, url, **kwargs)


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
    # aircraft_db and backup import DB_PATH by value (not the engine), so
    # they need their own patch target to see the per-test database file.
    monkeypatch.setattr("app.aircraft_db.DB_PATH", str(db_path))
    monkeypatch.setattr("app.backup.DB_PATH", str(db_path))
    monkeypatch.setattr("app.backup.BACKUP_DIR", str(tmp_path / "backups"))

    init_db()
    return engine


@pytest.fixture
def client(test_engine, monkeypatch):
    """A TestClient over the real app, but WITHOUT running the startup event
    (that would start the scheduler - background threads + real OpenSky
    requests - which tests neither need nor want). The DB is already
    initialized by the test_engine fixture above.

    reschedule_poll_job/reschedule_backup_job talk to the live APScheduler
    job store, which only exists once start_scheduler() has run - stubbed
    out since that's a separate concern from what these route tests check.
    """
    from app.main import app

    monkeypatch.setattr("app.main.reschedule_poll_job", lambda seconds: None)
    monkeypatch.setattr("app.main.reschedule_backup_job", lambda hours: None)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_enrichment_calls(monkeypatch):
    """Route enrichment (app/adsbdb.py) and photo enrichment
    (app/planespotters.py) each make a real HTTP call whenever a sighting
    matches. Default both to a no-op everywhere so tests that aren't
    specifically about one of these don't hit the network or flake on it;
    individual tests override this via monkeypatch when they need to assert
    on the enrichment itself.

    Rebinds the *names* `adsbdb`/`planespotters` inside app.scheduler's
    namespace rather than patching the real modules' functions directly -
    the latter would mutate the single shared module objects and break
    test_adsbdb_client.py/test_planespotters_client.py, which import and
    exercise the real functions directly.
    """

    class _StubAdsbdb:
        @staticmethod
        def fetch_route(callsign):
            return None

    class _StubPlanespotters:
        @staticmethod
        def fetch_photo(icao24):
            return None

    monkeypatch.setattr("app.scheduler.adsbdb", _StubAdsbdb)
    monkeypatch.setattr("app.scheduler.planespotters", _StubPlanespotters)


@pytest.fixture(autouse=True)
def _auto_csrf_token(monkeypatch):
    """Every POST route now requires a matching csrf_token in the submitted
    form (see app/csrf.py). Rather than editing every one of the dozens of
    existing `client.post(..., data={...})` call sites across the test suite
    - CSRF verification is a cross-cutting concern those tests aren't about -
    transparently fetch and inject a valid token into any POST that doesn't
    already carry one. Patches the TestClient *class*, not just the `client`
    fixture's instance, since several test files construct their own
    `TestClient(app)` (e.g. a second user "bob") that never goes through
    that fixture.

    Tests that need to exercise a missing/invalid token (tests/test_csrf.py)
    use `raw_post` above instead, which bypasses this entirely.
    """

    def post_with_csrf_token(self, url, *args, **kwargs):
        data = kwargs.get("data")
        if data is None or "csrf_token" not in data:
            data = dict(data) if data else {}
            token_page = self.get("/login")
            match = re.search(r'name="csrf_token" value="([^"]+)"', token_page.text)
            assert match, "no csrf_token found on /login"
            data.setdefault("csrf_token", match.group(1))
            kwargs["data"] = data
        return _ORIGINAL_TESTCLIENT_POST(self, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "post", post_with_csrf_token)


def register(client: TestClient, email: str, password: str = "testpassword1"):
    return client.post(
        "/register",
        data={"email": email, "password": password, "password_confirm": password},
        follow_redirects=False,
    )
