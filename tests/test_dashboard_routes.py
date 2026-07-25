from datetime import date, timedelta

from sqlmodel import Session, select

from app.main import _day_label
from app.models import Airport, AirportWatch, Sighting, SightingMatch, User
from tests.conftest import register


def _seed_sighting(test_engine, email: str, typecode: str | None) -> None:
    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        airport = Airport(icao="EDDF", name="Frankfurt", lat=50.0, lon=8.5)
        session.add(airport)
        session.commit()
        session.refresh(airport)
        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        sighting = Sighting(airport_id=airport.id, icao24="abc123", callsign="GAF123", typecode=typecode)
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(SightingMatch(sighting_id=sighting.id, category_key="military", label="Militär"))
        session.commit()


def test_dashboard_shows_skybrary_link_when_typecode_known(client, test_engine):
    register(client, "alice@example.com")
    _seed_sighting(test_engine, "alice@example.com", typecode="EUFI")

    page = client.get("/")

    assert 'href="https://skybrary.aero/aircraft/eufi"' in page.text
    assert "Info zum Typ" in page.text


def test_dashboard_omits_skybrary_link_without_typecode(client, test_engine):
    register(client, "alice@example.com")
    _seed_sighting(test_engine, "alice@example.com", typecode=None)

    page = client.get("/")

    assert "skybrary.aero" not in page.text


def test_dashboard_shows_route_info_when_available(client, test_engine):
    register(client, "alice@example.com")
    with Session(test_engine) as session:
        user = session.exec(select(User).where(User.email == "alice@example.com")).first()
        airport = Airport(icao="EDDF", name="Frankfurt", lat=50.0, lon=8.5)
        session.add(airport)
        session.commit()
        session.refresh(airport)
        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        sighting = Sighting(
            airport_id=airport.id,
            icao24="abc123",
            callsign="GAF123",
            typecode="EUFI",
            route_origin_icao="EDDF",
            route_origin_name="Frankfurt",
            route_destination_icao="EDDM",
            route_destination_name="München",
        )
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(SightingMatch(sighting_id=sighting.id, category_key="military", label="Militär"))
        session.commit()

    page = client.get("/")

    assert "Route: EDDF → EDDM" in page.text
    assert "Frankfurt → München" in page.text


def test_dashboard_omits_route_info_when_unavailable(client, test_engine):
    register(client, "alice@example.com")
    _seed_sighting(test_engine, "alice@example.com", typecode="EUFI")

    page = client.get("/")

    assert "Route:" not in page.text


def test_day_label_today():
    today = date(2026, 7, 25)
    assert _day_label(today, today) == "Heute"


def test_day_label_yesterday():
    today = date(2026, 7, 25)
    assert _day_label(today - timedelta(days=1), today) == "Gestern"


def test_day_label_older_uses_weekday_name():
    today = date(2026, 7, 25)  # a Saturday
    older = today - timedelta(days=3)  # the preceding Wednesday
    assert _day_label(older, today) == "Mittwoch"
