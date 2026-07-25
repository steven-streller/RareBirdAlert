from sqlmodel import Session

from app import flight_sources
from app.db import get_effective_setting, set_setting
from app.state_vector import StateVector


def test_get_effective_setting_uses_db_value_without_env(test_engine, monkeypatch):
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    with Session(test_engine) as session:
        set_setting(session, "opensky_client_id", "from-db")
        value, locked = get_effective_setting(session, "opensky_client_id")
    assert value == "from-db"
    assert locked is False


def test_get_effective_setting_env_wins_over_db(test_engine, monkeypatch):
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "from-env")
    with Session(test_engine) as session:
        set_setting(session, "opensky_client_id", "from-db")
        value, locked = get_effective_setting(session, "opensky_client_id")
    assert value == "from-env"
    assert locked is True


def test_get_effective_setting_ignores_empty_env(test_engine, monkeypatch):
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "")
    with Session(test_engine) as session:
        set_setting(session, "opensky_client_id", "from-db")
        value, locked = get_effective_setting(session, "opensky_client_id")
    assert value == "from-db"
    assert locked is False


def test_enabled_sources_reflects_settings(test_engine):
    with Session(test_engine) as session:
        assert set(flight_sources.enabled_sources(session)) == {"opensky", "adsblol", "airplaneslive"}
        set_setting(session, "source_enabled_adsblol", "false")
        assert set(flight_sources.enabled_sources(session)) == {"opensky", "airplaneslive"}


def test_fetch_merged_states_combines_two_sources(test_engine, monkeypatch):
    opensky_state = StateVector(icao24="abc123", callsign=None, on_ground=False, lat=50.0, lon=8.0)
    adsblol_state = StateVector(
        icao24="abc123",
        callsign="GAF123",
        on_ground=True,
        lat=50.01,
        lon=8.01,
        typecode="EUFI",
        registration="31+00",
        flagged_military=True,
    )

    monkeypatch.setitem(flight_sources.SOURCES["opensky"], "fetch", lambda cfg, lat, lon, r: [opensky_state])
    monkeypatch.setitem(flight_sources.SOURCES["adsblol"], "fetch", lambda cfg, lat, lon, r: [adsblol_state])

    with Session(test_engine) as session:
        set_setting(session, "source_enabled_airplaneslive", "false")
        merged = flight_sources.fetch_merged_states(session, 50.0, 8.0, 15.0)

    assert len(merged) == 1
    state = merged[0]
    # on_ground: OR across sources - adsb.lol's "on the ground" must not be
    # suppressed by OpenSky's (possibly stale) "still airborne".
    assert state.on_ground is True
    assert state.callsign == "GAF123"
    assert state.typecode == "EUFI"
    assert state.registration == "31+00"
    assert state.flagged_military is True


def test_fetch_merged_states_combines_three_sources(test_engine, monkeypatch):
    """The whole point of the SOURCES-registry design: a third source (here
    standing in for airplanes.live) needs no special-casing anywhere in the
    merge logic - fetch_merged_states just folds over however many sources
    are enabled."""
    opensky_state = StateVector(icao24="abc123", callsign=None, on_ground=False, lat=50.0, lon=8.0)
    adsblol_state = StateVector(icao24="abc123", callsign="GAF123", on_ground=False, typecode="EUFI")
    airplaneslive_state = StateVector(icao24="abc123", on_ground=True, registration="31+00", flagged_military=True)

    monkeypatch.setitem(flight_sources.SOURCES["opensky"], "fetch", lambda cfg, lat, lon, r: [opensky_state])
    monkeypatch.setitem(flight_sources.SOURCES["adsblol"], "fetch", lambda cfg, lat, lon, r: [adsblol_state])
    monkeypatch.setitem(
        flight_sources.SOURCES["airplaneslive"], "fetch", lambda cfg, lat, lon, r: [airplaneslive_state]
    )

    with Session(test_engine) as session:
        merged = flight_sources.fetch_merged_states(session, 50.0, 8.0, 15.0)

    assert len(merged) == 1
    state = merged[0]
    assert state.callsign == "GAF123"  # from adsblol_state
    assert state.typecode == "EUFI"  # from adsblol_state
    assert state.registration == "31+00"  # from airplaneslive_state
    assert state.flagged_military is True  # from airplaneslive_state
    assert state.on_ground is True  # OR across all three, airplaneslive_state wins


def test_fetch_merged_states_keeps_aircraft_seen_by_only_one_source(test_engine, monkeypatch):
    only_opensky = StateVector(icao24="abc123", on_ground=False, lat=50.0, lon=8.0)
    only_adsblol = StateVector(icao24="def456", on_ground=True, lat=50.0, lon=8.0)
    only_airplaneslive = StateVector(icao24="ghi789", on_ground=True, lat=50.0, lon=8.0)

    monkeypatch.setitem(flight_sources.SOURCES["opensky"], "fetch", lambda cfg, lat, lon, r: [only_opensky])
    monkeypatch.setitem(flight_sources.SOURCES["adsblol"], "fetch", lambda cfg, lat, lon, r: [only_adsblol])
    monkeypatch.setitem(
        flight_sources.SOURCES["airplaneslive"], "fetch", lambda cfg, lat, lon, r: [only_airplaneslive]
    )

    with Session(test_engine) as session:
        merged = flight_sources.fetch_merged_states(session, 50.0, 8.0, 15.0)

    assert {s.icao24 for s in merged} == {"abc123", "def456", "ghi789"}


def test_fetch_merged_states_skips_disabled_sources(test_engine, monkeypatch):
    called = []
    for key in flight_sources.SOURCES:
        monkeypatch.setitem(
            flight_sources.SOURCES[key], "fetch", lambda cfg, lat, lon, r, key=key: called.append(key) or []
        )

    with Session(test_engine) as session:
        set_setting(session, "source_enabled_adsblol", "false")
        set_setting(session, "source_enabled_airplaneslive", "false")
        flight_sources.fetch_merged_states(session, 50.0, 8.0, 15.0)

    assert called == ["opensky"]
