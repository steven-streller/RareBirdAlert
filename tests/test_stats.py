from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models import Airport, AirportWatch, Sighting, SightingMatch, User
from app.stats import compute_stats
from tests.conftest import register


def _seed(test_engine, email: str, sightings: list[dict]) -> None:
    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        airport = Airport(icao="EDDF", name="Frankfurt", lat=50.0, lon=8.5)
        session.add(airport)
        session.commit()
        session.refresh(airport)
        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        session.commit()

        for row in sightings:
            sighting = Sighting(
                airport_id=airport.id,
                icao24="abc123",
                callsign="GAF123",
                typecode=row.get("typecode"),
                landed_at=row["landed_at"],
            )
            session.add(sighting)
            session.commit()
            session.refresh(sighting)
            session.add(SightingMatch(sighting_id=sighting.id, category_key="military", label=row["label"]))
            session.commit()


def test_compute_stats_returns_zeroed_result_without_sightings(client, test_engine):
    register(client, "alice@example.com")
    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == "alice@example.com")).first()
        stats = compute_stats(session, user.id)

    assert stats["total"] == 0
    assert stats["first_seen"] is None
    assert stats["top_types"] == []
    assert stats["top_airports"] == []
    assert stats["top_labels"] == []
    assert stats["monthly"] == []


def test_compute_stats_aggregates_sightings(client, test_engine):
    register(client, "alice@example.com")
    now = datetime.utcnow()
    _seed(
        test_engine,
        "alice@example.com",
        [
            {"typecode": "EUFI", "label": "Militär", "landed_at": now},
            {"typecode": "EUFI", "label": "Militär", "landed_at": now - timedelta(days=1)},
            {"typecode": "A388", "label": "Airbus A380", "landed_at": now - timedelta(days=2)},
        ],
    )

    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == "alice@example.com")).first()
        stats = compute_stats(session, user.id)

    assert stats["total"] == 3
    assert stats["top_types"][0] == {"label": "EUFI", "count": 2}
    assert stats["top_airports"][0] == {"label": "EDDF", "name": "Frankfurt", "count": 3}
    assert stats["top_labels"][0] == {"label": "Militär", "count": 2}
    assert len(stats["monthly"]) == 12
    assert stats["monthly"][-1]["month"] == now.strftime("%Y-%m")
    assert sum(m["count"] for m in stats["monthly"]) == 3


def test_compute_stats_ignores_sightings_at_unwatched_airports(client, test_engine):
    register(client, "alice@example.com")
    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == "alice@example.com")).first()
        other_airport = Airport(icao="EDDM", name="Munich", lat=48.3, lon=11.8)
        session.add(other_airport)
        session.commit()
        session.refresh(other_airport)
        # Not watched by alice - AirportWatch deliberately omitted.
        session.add(Sighting(airport_id=other_airport.id, icao24="xyz", callsign="XYZ1", typecode="A320"))
        session.commit()

        stats = compute_stats(session, user.id)

    assert stats["total"] == 0


def test_stats_page_renders_empty_state(client):
    register(client, "alice@example.com")
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert "Noch keine Sichtungen" in resp.text


def test_stats_page_shows_totals(client, test_engine):
    register(client, "alice@example.com")
    _seed(test_engine, "alice@example.com", [{"typecode": "EUFI", "label": "Militär", "landed_at": datetime.utcnow()}])

    resp = client.get("/stats")
    assert resp.status_code == 200
    assert "Sichtungen insgesamt" in resp.text
    assert "EUFI" in resp.text


def test_stats_requires_login(client):
    resp = client.get("/stats", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
