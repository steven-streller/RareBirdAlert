from sqlmodel import Session, select

from app import metrics, scheduler
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


def test_process_state_increments_the_sightings_total_metric(test_engine, monkeypatch):
    # Counter is a process-wide singleton shared across the whole test
    # session, so assert on the delta rather than an absolute value.
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI"})
    before = metrics.sightings_total._value.get()

    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()
        state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

    after = metrics.sightings_total._value.get()
    assert after == before + 1


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
    """The airborne transition in the middle is now its own "departure"
    event (not just a silent state update) - matching aircraft get a
    Sighting for it just like for landing/approach/takeoff_roll."""
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

        sightings = session.exec(select(Sighting).order_by(Sighting.id)).all()
        assert [s.event_type for s in sightings] == ["landing", "departure", "landing"]


def test_process_state_creates_sighting_on_approach(test_engine, monkeypatch):
    """A strong sink rate while still airborne is a heads-up before the
    actual landing - no airport elevation is needed since fetch_merged_states
    already scopes the query to aircraft near the airport."""
    monkeypatch.setattr(
        scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI", "operator": "Luftwaffe"}
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(
            icao24="abc123", callsign="GAF123", on_ground=False, lat=50.0, lon=8.5, vertical_rate_fpm=-1200
        )
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sightings = session.exec(select(Sighting)).all()
        assert len(sightings) == 1
        assert sightings[0].event_type == "approach"


def test_process_state_does_not_retrigger_approach_while_still_descending(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI"})
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(
            icao24="abc123", callsign="GAF123", on_ground=False, lat=50.0, lon=8.5, vertical_rate_fpm=-1200
        )
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        assert len(session.exec(select(Sighting)).all()) == 1


def test_process_state_creates_takeoff_roll_sighting_when_accelerating(test_engine, monkeypatch):
    """Ground speed crossing the roll threshold while already on the ground
    (not the instant of touchdown) signals an aircraft accelerating for
    takeoff, ahead of the actual airborne transition."""
    monkeypatch.setattr(
        scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI", "operator": "Luftwaffe"}
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        # Already parked/taxiing on the ground - established directly rather
        # than via _process_state, so this setup doesn't itself create a
        # "landing" Sighting for a first-ever sighting.
        session.add(
            AircraftTrackState(icao24="abc123", airport_id=airport.id, on_ground=True, last_ground_speed_kt=10)
        )
        session.commit()

        rolling = StateVector(
            icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5, ground_speed_kt=60
        )
        scheduler._process_state(session, airport, rolling, [category], [])
        session.commit()

        sightings = session.exec(select(Sighting)).all()
        assert len(sightings) == 1
        assert sightings[0].event_type == "takeoff_roll"


def test_process_state_landing_rollout_does_not_trigger_takeoff_roll(test_engine, monkeypatch):
    """A fast landing rollout also reads as "on ground and fast" - must not
    be mistaken for a takeoff roll just because it's above the speed
    threshold; only an upward crossing of the threshold counts as one."""
    monkeypatch.setattr(
        scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI", "operator": "Luftwaffe"}
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        landed_fast = StateVector(
            icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5, ground_speed_kt=90
        )
        scheduler._process_state(session, airport, landed_fast, [category], [])
        session.commit()

        still_decelerating = StateVector(
            icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5, ground_speed_kt=70
        )
        scheduler._process_state(session, airport, still_decelerating, [category], [])
        session.commit()

        sightings = session.exec(select(Sighting).order_by(Sighting.id)).all()
        assert [s.event_type for s in sightings] == ["landing"]


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


def test_notify_check_job_includes_route_info_in_message(test_engine, monkeypatch):
    captured = {}

    def fake_notify_all(session, user_id, title, message, url=None):
        captured["message"] = message
        return {"webhook": True}

    monkeypatch.setattr(scheduler, "notify_all", fake_notify_all)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="route-watcher@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        set_user_setting(session, user.id, "webhook_enabled", "true")

        sighting = Sighting(
            airport_id=airport.id,
            icao24="abc123",
            callsign="GAF123",
            typecode="EUFI",
            route_origin_icao="EDDF",
            route_destination_icao="EDDM",
        )
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(
            SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter Typhoon")
        )
        session.commit()

    scheduler.notify_check_job()

    assert "Route: EDDF → EDDM" in captured["message"]


def test_notify_check_job_passes_the_photo_link_as_the_notification_url(test_engine, monkeypatch):
    captured = {}

    def fake_notify_all(session, user_id, title, message, url=None):
        captured["url"] = url
        return {"webhook": True}

    monkeypatch.setattr(scheduler, "notify_all", fake_notify_all)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="photo-watcher@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        set_user_setting(session, user.id, "webhook_enabled", "true")

        sighting = Sighting(
            airport_id=airport.id,
            icao24="abc123",
            callsign="GAF123",
            typecode="EUFI",
            photo_link="https://www.planespotters.net/photo/1",
        )
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(
            SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter Typhoon")
        )
        session.commit()

    scheduler.notify_check_job()

    assert captured["url"] == "https://www.planespotters.net/photo/1"


def test_notify_check_job_omits_route_line_when_no_route_found(test_engine, monkeypatch):
    captured = {}

    def fake_notify_all(session, user_id, title, message, url=None):
        captured["message"] = message
        return {"webhook": True}

    monkeypatch.setattr(scheduler, "notify_all", fake_notify_all)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="no-route-watcher@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)

        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        set_user_setting(session, user.id, "webhook_enabled", "true")

        sighting = Sighting(airport_id=airport.id, icao24="abc123", callsign="GAF123", typecode="EUFI")
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(
            SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter Typhoon")
        )
        session.commit()

    scheduler.notify_check_job()

    assert "Route:" not in captured["message"]


def _seed_notifiable_sighting(session, airport, email):
    user = User(email=email, password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
    set_user_setting(session, user.id, "webhook_enabled", "true")

    sighting = Sighting(airport_id=airport.id, icao24="abc123", callsign="GAF123", typecode="EUFI")
    session.add(sighting)
    session.commit()
    session.refresh(sighting)
    session.add(SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter"))
    session.commit()
    return user


def test_notify_check_job_skips_during_quiet_hours_without_marking_notified(test_engine, monkeypatch):
    from datetime import datetime as real_datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def utcnow(cls):
            return real_datetime(2026, 7, 25, 23, 0)  # 23:00 UTC - inside a 22:00-07:00 UTC window

    monkeypatch.setattr(scheduler, "datetime", _FrozenDatetime)
    monkeypatch.setattr(
        scheduler, "notify_all", lambda session, user_id, title, message, url=None: {"webhook": True}
    )

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = _seed_notifiable_sighting(session, airport, "quiet-hours@example.com")
        set_user_setting(session, user.id, "quiet_hours_enabled", "true")
        set_user_setting(session, user.id, "quiet_hours_start", "22:00")
        set_user_setting(session, user.id, "quiet_hours_end", "07:00")
        set_user_setting(session, user.id, "quiet_hours_timezone", "UTC")

    scheduler.notify_check_job()

    with Session(test_engine) as session:
        assert session.exec(select(NotificationLog)).all() == []


def test_notify_check_job_delivers_normally_outside_quiet_hours(test_engine, monkeypatch):
    from datetime import datetime as real_datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def utcnow(cls):
            return real_datetime(2026, 7, 25, 12, 0)  # 12:00 UTC - outside a 22:00-07:00 UTC window

    monkeypatch.setattr(scheduler, "datetime", _FrozenDatetime)
    monkeypatch.setattr(
        scheduler, "notify_all", lambda session, user_id, title, message, url=None: {"webhook": True}
    )

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = _seed_notifiable_sighting(session, airport, "daytime@example.com")
        set_user_setting(session, user.id, "quiet_hours_enabled", "true")
        set_user_setting(session, user.id, "quiet_hours_start", "22:00")
        set_user_setting(session, user.id, "quiet_hours_end", "07:00")
        set_user_setting(session, user.id, "quiet_hours_timezone", "UTC")

    scheduler.notify_check_job()

    with Session(test_engine) as session:
        assert len(session.exec(select(NotificationLog)).all()) == 1


def test_notify_check_job_ignores_quiet_hours_when_disabled(test_engine, monkeypatch):
    from datetime import datetime as real_datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def utcnow(cls):
            return real_datetime(2026, 7, 25, 23, 0)  # would be inside the window, but it's disabled

    monkeypatch.setattr(scheduler, "datetime", _FrozenDatetime)
    monkeypatch.setattr(
        scheduler, "notify_all", lambda session, user_id, title, message, url=None: {"webhook": True}
    )

    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = _seed_notifiable_sighting(session, airport, "disabled-quiet-hours@example.com")
        set_user_setting(session, user.id, "quiet_hours_enabled", "false")
        set_user_setting(session, user.id, "quiet_hours_start", "22:00")
        set_user_setting(session, user.id, "quiet_hours_end", "07:00")
        set_user_setting(session, user.id, "quiet_hours_timezone", "UTC")

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


def test_process_state_enriches_sighting_with_route_on_match(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler.aircraft_db,
        "lookup",
        lambda icao24: {"typecode": "EUFI", "registration": "31+00", "operator": "Luftwaffe"},
    )
    captured_callsigns = []

    def fake_fetch_route(callsign):
        captured_callsigns.append(callsign)
        # airport is EDDF (see _make_airport default) and this is a landing
        # there, so the destination must be EDDF for the plausibility check
        # in _route_is_plausible to accept it - see the dedicated tests below
        # for what happens when it doesn't match.
        return {
            "origin_icao": "EDDM",
            "origin_name": "München",
            "destination_icao": "EDDF",
            "destination_name": "Frankfurt",
        }

    monkeypatch.setattr(scheduler.adsbdb, "fetch_route", fake_fetch_route)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.route_origin_icao == "EDDM"
        assert sighting.route_origin_name == "München"
        assert sighting.route_destination_icao == "EDDF"
    assert captured_callsigns == ["GAF123"]


def test_process_state_drops_route_that_disagrees_with_the_landing_airport(test_engine, monkeypatch):
    """Regression test: adsbdb.com's route is schedule data keyed by
    callsign, not tied to this specific flight/day, and can flatly disagree
    with reality (e.g. a callsign reused for a different rotation). A route
    whose destination isn't the airport we're actually landing at must be
    dropped rather than shown as if it were correct."""
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "A320"})
    monkeypatch.setattr(
        scheduler.adsbdb,
        "fetch_route",
        lambda callsign: {
            "origin_icao": "EDDF",
            "origin_name": "Frankfurt",
            "destination_icao": "LBSF",
            "destination_name": "Sofia",
        },
    )

    with Session(test_engine) as session:
        airport = _make_airport(session, icao="EDDW")  # actual landing airport, not Sofia
        user = User(email="watcher@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        entry = WatchlistEntry(user_id=user.id, label="A320 Fan", match_type="typecode", pattern="A320")
        session.add(entry)
        session.commit()
        session.refresh(entry)

        state = StateVector(icao24="abc123", callsign="DLH3EE", on_ground=True, lat=53.0, lon=8.8)
        scheduler._process_state(session, airport, state, [], [entry])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.route_origin_icao is None
        assert sighting.route_destination_icao is None


def test_process_state_keeps_route_for_departure_matching_origin(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "A320"})
    monkeypatch.setattr(
        scheduler.adsbdb,
        "fetch_route",
        lambda callsign: {
            "origin_icao": "EDDW",
            "origin_name": "Bremen",
            "destination_icao": "EDDM",
            "destination_name": "München",
        },
    )

    with Session(test_engine) as session:
        airport = _make_airport(session, icao="EDDW")
        user = User(email="watcher@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        entry = WatchlistEntry(user_id=user.id, label="A320 Fan", match_type="typecode", pattern="A320")
        session.add(entry)

        # Already rolling on the ground -> the next fast reading is a takeoff roll.
        session.add(
            AircraftTrackState(icao24="abc123", airport_id=airport.id, on_ground=True, last_ground_speed_kt=10)
        )
        session.commit()
        state = StateVector(
            icao24="abc123", callsign="DLH3EE", on_ground=True, lat=53.0, lon=8.8, ground_speed_kt=60
        )
        scheduler._process_state(session, airport, state, [], [entry])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.event_type == "takeoff_roll"
        assert sighting.route_origin_icao == "EDDW"
        assert sighting.route_destination_icao == "EDDM"


def test_process_state_skips_route_lookup_without_callsign(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI"})

    def fail(*a, **k):
        raise AssertionError("must not look up a route without a callsign")

    monkeypatch.setattr(scheduler.adsbdb, "fetch_route", fail)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(icao24="abc123", callsign=None, on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.route_origin_icao is None


def test_process_state_enriches_sighting_with_photo_on_match(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler.aircraft_db,
        "lookup",
        lambda icao24: {"typecode": "EUFI", "registration": "31+00", "operator": "Luftwaffe"},
    )
    captured_icao24s = []

    def fake_fetch_photo(icao24):
        captured_icao24s.append(icao24)
        return {
            "thumbnail_url": "https://t.plnspttrs.net/x_t.jpg",
            "large_url": "https://t.plnspttrs.net/x_280.jpg",
            "link": "https://www.planespotters.net/photo/1",
        }

    monkeypatch.setattr(scheduler.planespotters, "fetch_photo", fake_fetch_photo)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.photo_thumbnail_url == "https://t.plnspttrs.net/x_t.jpg"
        assert sighting.photo_large_url == "https://t.plnspttrs.net/x_280.jpg"
        assert sighting.photo_link == "https://www.planespotters.net/photo/1"
    assert captured_icao24s == ["abc123"]


def test_process_state_leaves_photo_fields_empty_when_none_found(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: {"typecode": "EUFI"})
    monkeypatch.setattr(scheduler.planespotters, "fetch_photo", lambda icao24: None)

    with Session(test_engine) as session:
        airport = _make_airport(session)
        category = session.exec(
            select(AircraftCategory).where(AircraftCategory.key == "eurofighter_typhoon")
        ).first()

        state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, lat=50.0, lon=8.5)
        scheduler._process_state(session, airport, state, [category], [])
        session.commit()

        sighting = session.exec(select(Sighting)).first()
        assert sighting.photo_thumbnail_url is None
        assert sighting.photo_link is None


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


def test_process_state_matches_custom_watchlist_entry_directly(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: None)
    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="collector@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        entry = WatchlistEntry(user_id=user.id, label="DC-3 Fan", match_type="typecode", pattern="DC3")
        session.add(entry)
        session.commit()
        session.refresh(entry)

        state = StateVector(icao24="abc123", callsign="OLD1", on_ground=True, typecode="DC3")
        # categories=[] - only the watchlist_entries branch (not the category
        # loop) can produce this match.
        scheduler._process_state(session, airport, state, [], [entry])
        session.commit()

        matches_ = session.exec(select(SightingMatch)).all()
        assert len(matches_) == 1
        assert matches_[0].watchlist_entry_id == entry.id
        assert matches_[0].category_key is None


def test_poll_job_returns_early_without_any_watches(test_engine, monkeypatch):
    called = []
    monkeypatch.setattr(scheduler.flight_sources, "fetch_merged_states", lambda *a, **k: called.append(1) or [])

    scheduler.poll_job()

    assert called == []


def test_poll_job_creates_sighting_for_watched_airport(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler.aircraft_db, "lookup", lambda icao24: None)
    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="poll-test@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=20))
        session.commit()

    captured_calls = []
    state = StateVector(icao24="abc123", callsign="GAF123", on_ground=True, typecode="EUFI")

    def fake_fetch(session, lat, lon, radius_km):
        captured_calls.append((round(lat, 2), round(lon, 2), radius_km))
        return [state]

    monkeypatch.setattr(scheduler.flight_sources, "fetch_merged_states", fake_fetch)

    scheduler.poll_job()

    assert captured_calls == [(50.0, 8.5, 20.0)]
    with Session(test_engine) as session:
        sightings = session.exec(select(Sighting)).all()
        assert len(sightings) == 1
        assert sightings[0].typecode == "EUFI"


def test_poll_job_cleans_up_stale_track_state(test_engine, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.setattr(scheduler.flight_sources, "fetch_merged_states", lambda *a, **k: [])
    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="stale-test@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        stale_time = datetime.utcnow() - timedelta(seconds=scheduler.STALE_TRACK_SECONDS + 60)
        session.add(
            AircraftTrackState(icao24="stale01", airport_id=airport.id, on_ground=True, last_seen_at=stale_time)
        )
        session.commit()

    scheduler.poll_job()

    with Session(test_engine) as session:
        assert session.exec(select(AircraftTrackState)).all() == []


def test_notify_check_job_returns_early_without_sightings(test_engine):
    # must not raise even though there's nothing to do
    scheduler.notify_check_job()


def test_notify_check_job_skips_user_without_enabled_channels(test_engine):
    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="no-channels@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(AirportWatch(user_id=user.id, airport_id=airport.id, radius_km=15))
        sighting = Sighting(airport_id=airport.id, icao24="abc123", callsign="GAF123", typecode="EUFI")
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter"))
        session.commit()

    scheduler.notify_check_job()  # no channels enabled anywhere - must not raise or notify

    with Session(test_engine) as session:
        assert session.exec(select(NotificationLog)).all() == []


def test_notify_check_job_skips_user_without_watched_airports(test_engine, monkeypatch):
    monkeypatch.setattr(
        scheduler, "notify_all", lambda session, user_id, title, message, url=None: {"webhook": True}
    )
    with Session(test_engine) as session:
        airport = _make_airport(session)
        user = User(email="no-watches@example.com", password_hash="x")
        session.add(user)
        session.commit()
        session.refresh(user)
        set_user_setting(session, user.id, "webhook_enabled", "true")  # channel enabled, but no AirportWatch

        sighting = Sighting(airport_id=airport.id, icao24="abc123", callsign="GAF123", typecode="EUFI")
        session.add(sighting)
        session.commit()
        session.refresh(sighting)
        session.add(SightingMatch(sighting_id=sighting.id, category_key="eurofighter_typhoon", label="Eurofighter"))
        session.commit()

    scheduler.notify_check_job()

    with Session(test_engine) as session:
        assert session.exec(select(NotificationLog)).all() == []


def test_reschedule_poll_job_changes_the_interval():
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.scheduler.add_job(lambda: None, trigger=IntervalTrigger(seconds=60), id="poll_job")
    try:
        scheduler.reschedule_poll_job(120)
        job = scheduler.scheduler.get_job("poll_job")
        assert job.trigger.interval.total_seconds() == 120
    finally:
        scheduler.scheduler.remove_job("poll_job")


def test_start_scheduler_registers_all_jobs(test_engine, monkeypatch):
    monkeypatch.setattr(scheduler, "poll_job", lambda: None)
    monkeypatch.setattr(scheduler, "notify_check_job", lambda: None)
    monkeypatch.setattr(scheduler.aircraft_db, "refresh_aircraft_db", lambda: None)
    monkeypatch.setattr(scheduler.backup, "run_backup", lambda: None)

    try:
        scheduler.start_scheduler()
        job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
        assert job_ids == {"poll_job", "notify_check_job", "aircraft_db_refresh_job", "backup_job"}
    finally:
        scheduler.scheduler.shutdown(wait=False)


def test_reschedule_backup_job_changes_the_interval():
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.scheduler.add_job(lambda: None, trigger=IntervalTrigger(hours=24), id="backup_job")
    try:
        scheduler.reschedule_backup_job(6)
        job = scheduler.scheduler.get_job("backup_job")
        assert job.trigger.interval.total_seconds() == 6 * 3600
    finally:
        scheduler.scheduler.remove_job("backup_job")
