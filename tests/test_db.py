from sqlmodel import Session

from app import db


def test_set_sqlite_pragma_configures_wal_and_busy_timeout():
    """Exercised for real on every new connection to the module-level
    engine, but tests always run against a separate per-test engine (see
    conftest.test_engine) that never triggers this listener - so it's worth
    a direct unit test of the callback itself."""

    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql):
            self.executed.append(sql)

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def cursor(self):
            return self.cursor_obj

    conn = FakeConnection()
    db._set_sqlite_pragma(conn, None)

    assert conn.cursor_obj.executed == ["PRAGMA journal_mode=WAL", "PRAGMA busy_timeout=30000"]


def test_set_setting_creates_row_for_a_never_seeded_key(test_engine):
    with Session(test_engine) as session:
        db.set_setting(session, "brand_new_key", "value")
        assert db.get_setting(session, "brand_new_key") == "value"


def test_search_airport_directory_requires_at_least_two_characters():
    assert db.search_airport_directory("E") == []
    assert db.search_airport_directory("") == []


def test_search_airport_directory_respects_limit():
    # "AA" as an ICAO prefix or name substring matches over a hundred entries
    # in the bundled directory - this exercises the early-break once `limit`
    # results have been collected.
    results = db.search_airport_directory("AA", limit=5)
    assert len(results) == 5
