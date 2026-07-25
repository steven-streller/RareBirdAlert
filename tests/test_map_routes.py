from sqlmodel import Session, select

from app import flight_sources
from app.models import AirportWatch, User
from app.state_vector import StateVector
from tests.conftest import register


def test_map_page_renders_without_airports(client):
    register(client, "alice@example.com")
    resp = client.get("/map")
    assert resp.status_code == 200
    assert "Noch keine Flughäfen beobachtet." in resp.text


def test_map_page_includes_watch_data(client):
    register(client, "alice@example.com")
    client.post("/airports", data={"icao": "EDDF", "radius_km": "20"})

    resp = client.get("/map")
    assert resp.status_code == 200
    assert '"icao": "EDDF"' in resp.text
    assert '"radius_km": 20.0' in resp.text


def test_map_live_returns_empty_without_watches(client):
    register(client, "alice@example.com")
    resp = client.get("/map/live")
    assert resp.status_code == 200
    assert resp.json() == {"aircraft": []}


def test_map_live_returns_aircraft_from_merged_states(client, monkeypatch):
    register(client, "alice@example.com")
    client.post("/airports", data={"icao": "EDDF", "radius_km": "15"})

    state = StateVector(
        icao24="3c6444",
        callsign="GAF123",
        on_ground=True,
        lat=50.05,
        lon=8.55,
        typecode="EUFI",
        registration="31+00",
        flagged_military=True,
    )
    monkeypatch.setattr(flight_sources, "fetch_merged_states", lambda session, lat, lon, r: [state])

    resp = client.get("/map/live")
    assert resp.status_code == 200
    aircraft = resp.json()["aircraft"]
    assert len(aircraft) == 1
    assert aircraft[0]["icao24"] == "3c6444"
    assert aircraft[0]["callsign"] == "GAF123"
    assert aircraft[0]["lat"] == 50.05
    assert aircraft[0]["typecode"] == "EUFI"
    assert aircraft[0]["flagged_military"] is True
    assert aircraft[0]["airport_icao"] == "EDDF"


def test_map_live_skips_aircraft_without_position(client, monkeypatch):
    register(client, "alice@example.com")
    client.post("/airports", data={"icao": "EDDF", "radius_km": "15"})

    state = StateVector(icao24="3c6444", on_ground=False, lat=None, lon=None)
    monkeypatch.setattr(flight_sources, "fetch_merged_states", lambda session, lat, lon, r: [state])

    resp = client.get("/map/live")
    assert resp.json() == {"aircraft": []}


def test_map_live_queries_each_watched_airport_once(client, monkeypatch):
    register(client, "alice@example.com")
    client.post("/airports", data={"icao": "EDDF", "radius_km": "10"})
    client.post("/airports", data={"icao": "EDDM", "radius_km": "10"})

    calls = []

    def fake_fetch(session, lat, lon, radius_km):
        calls.append((round(lat, 2), round(lon, 2)))
        return []

    monkeypatch.setattr(flight_sources, "fetch_merged_states", fake_fetch)

    client.get("/map/live")

    assert len(calls) == 2


def test_map_live_requires_login(client):
    resp = client.get("/map/live", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_map_live_skips_watch_referencing_a_missing_airport(client, test_engine, monkeypatch):
    register(client, "alice@example.com")

    called = []
    monkeypatch.setattr(flight_sources, "fetch_merged_states", lambda *a, **k: called.append(1) or [])

    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == "alice@example.com")).first()
        # No matching Airport row exists for this id - a defensive edge case
        # (dangling reference), not something the normal add-airport flow can produce.
        session.add(AirportWatch(user_id=user.id, airport_id=999999, radius_km=15))
        session.commit()

    resp = client.get("/map/live")

    assert resp.json() == {"aircraft": []}
    assert called == []
