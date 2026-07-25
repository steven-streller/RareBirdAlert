from sqlmodel import Session, select

from app import scheduler
from app.db import set_user_setting
from app.models import (
    AircraftCategory,
    AircraftTrackState,
    Airport,
    AirportWatch,
    NotificationLog,
    Sighting,
    SightingMatch,
    User,
    WatchlistEntry,
)
from app.state_vector import StateVector


def _make_airport(session, icao="EDDF"):
    airport = Airport(icao=icao, name="Frankfurt", lat=50.0, lon=8.5)
    session.add(airport)
    session.commit()
    session.refresh(airport)
    return airport


def test_process_state_creates_sighting_on_landing(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler.aircraft_db,
        "lookup",
        lambda icao24: {"typecode": "EUFI", "registration": "31+00", "operator": "Luftwaffe"},
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sightings = session.exec(select(Sighting)).all()
        assert len(sightings) == 1
        assert sightings[0].typecode == "EUFI"

        matches = session.exec(select(SightingMatch)).all()
        assert len(matches) == 1
        assert matches[0].category_key == "eurofighter_typhoon"


def test_process_state_does_not_retrigger_while_parked(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI", "operator": "Luftwaffe"}
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        assert len(session.exec(select(Sighting)).all()) == 1


def test_process_state_retriggers_after_takeoff_and_landing_again(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI", "operator": "Luftwaffe"}
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        landed = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        airborne = StateVector(icao24="abc123", callsign="GAF123", on_ground=False, lat=50.0, lon=8.5)

        scheduler._process_state(session, airport, landed, [category], [])
        session.commit()
        scheduler._process_state(session, airport, airborne, [category], [])
        session.commit()
        scheduler._process_state(session, airport, landed, [category], [])
        session.commit()

        assert len(session.exec(select(Sighting)).all()) == 2


def test_process_state_without_any_match_creates_no_sighting(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "A320"})
    with Session(test_engine) as session:
        airport = _make_airport(session)
        state = StateVector(icao24="abc123", callsign="DLH1", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [], [])
        session.commit()

        assert session.exec(select(Sighting)).all() == []
        track = session.exec(select(AircraftTrackState)).first()
        assert track is not None
        assert track.on_ground is True


def test_notify_check_job_respects_category_toggle_and_dedup(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler, "notify_all", lambda session, user_id, title, message, url=None: {"webhook": True}
    )

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user1 = User(email="watches@example.com", password_hash="x")
        user2 = User(email="disabled-category@example.com", password_hash="x")
        session.add(user1)
        session.add(user2)
        session.commit()
        session.refresh(user1)
        session.refresh(user2)

        session.add(AirportWatch(user_id=user1.id, airport_id=airport.id, radius_km=15))
        session.add(AirportWatch(user_id=user2.id, airport_id=airport.id, radius_km=15))
        session.commit()

        set_user_setting(session, user1.id, "webhook_enabled", "true")
        set_user_setting(session, user2.id, "webhook_enabled", "true")
        set_user_setting(session, user2.id, "category_enabled_eurofighter_typhoon", "false")

        sighting = Sighting(airport_id=airport.id, icao24="abc123", callsign="GAF123", typecode="EUFI")
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(
            SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter Typhoon")
        )
        session.commit()
        user1_id = user1.id

    scheduler.notify_check_job()

    with Session(test_engine) as session:
        logs = session.exec(select(NotificationLog)).all()
        assert len(logs) == 1
        assert logs[0].user_id == user1_id

    # Running again must not create a second notification for the same sighting.
    scheduler.notify_check_job()
    with Session(test_engine) as session:
        assert len(session.exec(select(NotificationLog)).all()) == 1


def test_notify_check_job_matches_custom_watchlist_entry(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler, "notify_all", lambda session, user_id, title, message, url=None: {"webhook": True}
    )

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="collector@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        set_user_setting(session, user.id, "webhook_enabled", "true")
        entry = WatchlistEntry(user_id=user.id, label="DC-3 Fan", match_type="typecode", pattern="DC3")
        session.add(entry)
        session.commit()
        session.refresh(entry)

        sighting = Sighting(airport_id=airport.id, icao24="def456", callsign="OLD1", typecode="DC3")
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(SightingMatch(sighting_id=sighting.id, watchlist_entry_id=entry.id, label="DC-3 Fan"))
        session.commit()

    scheduler.notify_check_job()

    with Session(test_engine) as session:
        assert len(session.exec(select(NotificationLog)).all()) == 1


def test_process_state_prefers_live_type_and_registration_over_metadata_db(test_engine, monkeypatch):
    # aircraft_db (OpenSky metadata cache) says one thing, but a live source
    # like adsb.lol reported a fresher/more specific value directly on the
    # StateVector - that should win.
    monkeypatch.setattr(
        scheduler.aircraft_db,
        "lookup",
        lambda icao24: {"typecode": "A332", "registration": "STALE-REG", "operator": "Luftwaffe"},
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(
            icao24="abc123",
            callsign="GAF123",
            on_ground=True,
            lat=50.0,
            lon=8.5,
            typecode="EUFI",
            registration="31+00",
        )
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.typecode == "EUFI"
        assert sighting.registration == "31+00"


def test_process_state_matches_adsb_flagged_category(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: None)
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(select(AircraftCategory).where(AircraftCategory.key == "adsb_flagged")).first()

        state = StateVector(
            icao24="abc123", callsign="UNKNOWN1", on_ground=True, lat=50.0, lon=8.5, flagged_pia=True
        )
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        matches_ = session.exec(select(SightingMatch)).all()
        assert len(matches_) == 1
        assert matches_[0].category_key == "adsb_flagged"
