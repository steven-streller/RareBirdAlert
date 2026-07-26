import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from app import adsbdb, aircraft_db, backup, flight_sources, metrics, planespotters
from app.db import engine, get_setting, get_user_setting
from app.matcher import AircraftInfo, matches
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
from app.notifications import enabled_channels, notify_all
from app.quiet_hours import is_within_quiet_hours
from app.state_vector import StateVector

logger = logging.getLogger("rarebirdalert.scheduler")

scheduler = BackgroundScheduler()

# How long an aircraft can go unseen at an airport before we forget its
# ground state - after this, a later reappearance counts as a fresh landing
# instead of being suppressed as "already on the ground".
STALE_TRACK_SECONDS = 6 * 3600

# Heuristic thresholds for the two "early" events below. There's no airport
# elevation anywhere in the data model (app/data/airports.csv doesn't carry
# one), so "height above ground" isn't computable - instead we lean on the
# fact that app.flight_sources.fetch_merged_states already scopes queries to
# a radius around the airport, and use vertical rate / ground speed as
# proxies for "this is an approach/takeoff, not cruise traffic passing by".
# Deliberately fixed, not user-configurable - tune here if they misfire.
APPROACH_DESCENT_RATE_FPM = -500
TAKEOFF_ROLL_SPEED_KT = 40

EVENT_LOG_VERBS = {
    "approach": "is on approach to",
    "landing": "landed at",
    "takeoff_roll": "is rolling for takeoff at",
    "departure": "departed",
}

# German copy for the user-facing notification (see notify_check_job below).
EVENT_TITLE_PREFIXES = {
    "approach": "Anflug",
    "landing": "Landung",
    "takeoff_roll": "Startrollen",
    "departure": "Start",
}
EVENT_MESSAGE_PHRASES = {
    "approach": "befindet sich im Landeanflug auf {airport}",
    "landing": "ist in {airport} gelandet",
    "takeoff_roll": "rollt in {airport} zum Start",
    "departure": "ist in {airport} gestartet",
}

ARRIVAL_EVENT_TYPES = {"approach", "landing"}
DEPARTURE_EVENT_TYPES = {"takeoff_roll", "departure"}


def _route_is_plausible(route: dict, airport_icao: str, event_type: str) -> bool:
    """adsbdb.com's route is schedule data keyed by callsign, not tied to
    this specific flight/day - airlines reuse callsigns across different
    rotations (wet-lease, aircraft swaps, ad-hoc schedule changes), so it can
    flatly disagree with the airport we're actually observing the aircraft
    at. Cross-checking against that airport can't confirm a route is right,
    but it can catch the case where it's clearly wrong for *this* sighting -
    e.g. an approach to EDDW where adsbdb claims the destination is LBSF.
    """
    if event_type in ARRIVAL_EVENT_TYPES and route.get("destination_icao"):
        return route["destination_icao"].upper() == airport_icao.upper()
    if event_type in DEPARTURE_EVENT_TYPES and route.get("origin_icao"):
        return route["origin_icao"].upper() == airport_icao.upper()
    return True


def _process_state(
    session: Session,
    airport: Airport,
    state: StateVector,
    categories: list[AircraftCategory],
    watchlist_entries: list[WatchlistEntry],
) -> None:
    track = session.exec(
        select(AircraftTrackState).where(
            AircraftTrackState.icao24 == state.icao24, AircraftTrackState.airport_id == airport.id
        )
    ).first()

    was_on_ground = track.on_ground if track else False
    previous_ground_speed_kt = track.last_ground_speed_kt if track else None
    approach_already_notified = track.approach_notified if track else False
    rolling_already_notified = track.rolling_notified if track else False

    landed_event = state.on_ground and not was_on_ground
    departed_event = was_on_ground and not state.on_ground
    approach_event = (
        not state.on_ground
        and not approach_already_notified
        and state.vertical_rate_fpm is not None
        and state.vertical_rate_fpm <= APPROACH_DESCENT_RATE_FPM
    )
    takeoff_roll_event = (
        state.on_ground
        and not landed_event  # a fast landing rollout isn't a takeoff roll
        and not rolling_already_notified
        and state.ground_speed_kt is not None
        and state.ground_speed_kt >= TAKEOFF_ROLL_SPEED_KT
        # Must cross the threshold while accelerating - a plane that just
        # landed also reads "on ground and fast", but only ever decelerates,
        # so it never crosses upward like an aircraft accelerating for takeoff.
        and previous_ground_speed_kt is not None
        and previous_ground_speed_kt < TAKEOFF_ROLL_SPEED_KT
    )

    if track:
        track.on_ground = state.on_ground
        track.last_seen_at = datetime.utcnow()
        track.last_ground_speed_kt = state.ground_speed_kt
        # Reset each one-shot flag once its "finished" counterpart happens,
        # so a later approach/takeoff roll can notify again.
        track.approach_notified = approach_event if landed_event else (track.approach_notified or approach_event)
        track.rolling_notified = takeoff_roll_event if departed_event else (
            track.rolling_notified or takeoff_roll_event
        )
        session.add(track)
    else:
        session.add(
            AircraftTrackState(
                icao24=state.icao24,
                airport_id=airport.id,
                on_ground=state.on_ground,
                last_ground_speed_kt=state.ground_speed_kt,
                approach_notified=approach_event,
                rolling_notified=takeoff_roll_event,
            )
        )

    if landed_event:
        event_type = "landing"
    elif departed_event:
        event_type = "departure"
    elif approach_event:
        event_type = "approach"
    elif takeoff_roll_event:
        event_type = "takeoff_roll"
    else:
        return

    # Live sources (e.g. adsb.lol) sometimes provide type/registration
    # directly - prefer those over the metadata-DB cache, which only ever
    # covers OpenSky (no live type/reg) and can lag behind a live re-registration.
    meta = aircraft_db.lookup(state.icao24) or {}
    aircraft = AircraftInfo(
        icao24=state.icao24,
        callsign=state.callsign,
        registration=state.registration or meta.get("registration"),
        typecode=state.typecode or meta.get("typecode"),
        operator=meta.get("operator"),
        flagged_military=state.flagged_military,
        flagged_pia=state.flagged_pia,
        flagged_ladd=state.flagged_ladd,
    )

    # (category_key, watchlist_entry_id, display label) for everything that matched
    match_hits: list[tuple[str | None, int | None, str]] = []
    for category in categories:
        if matches(category.match_type, category.pattern, aircraft):
            match_hits.append((category.key, None, category.label))
    for entry in watchlist_entries:
        if matches(entry.match_type, entry.pattern, aircraft):
            match_hits.append((None, entry.id, entry.label))

    if not match_hits:
        return

    # Best-effort only, and only for aircraft that actually matched - looking
    # this up for every polled aircraft would be needless load against a
    # free, unauthenticated API for traffic nobody cares about.
    route = adsbdb.fetch_route(state.callsign) if state.callsign else None
    if route and not _route_is_plausible(route, airport.icao, event_type):
        logger.info(
            "Dropping implausible adsbdb route for %s (%s at %s): %s -> %s",
            state.callsign,
            event_type,
            airport.icao,
            route.get("origin_icao"),
            route.get("destination_icao"),
        )
        route = None
    photo = planespotters.fetch_photo(state.icao24)

    sighting = Sighting(
        airport_id=airport.id,
        icao24=state.icao24,
        callsign=state.callsign,
        registration=aircraft.registration,
        typecode=aircraft.typecode,
        operator=aircraft.operator,
        event_type=event_type,
        route_origin_icao=route.get("origin_icao") if route else None,
        route_origin_name=route.get("origin_name") if route else None,
        route_destination_icao=route.get("destination_icao") if route else None,
        route_destination_name=route.get("destination_name") if route else None,
        photo_thumbnail_url=photo.get("thumbnail_url") if photo else None,
        photo_large_url=photo.get("large_url") if photo else None,
        photo_link=photo.get("link") if photo else None,
    )
    session.add(sighting)
    metrics.sightings_total.inc()
    session.flush()  # assigns sighting.id, needed for the SightingMatch rows below

    for category_key, watchlist_entry_id, label in match_hits:
        session.add(
            SightingMatch(
                sighting_id=sighting.id,
                category_key=category_key,
                watchlist_entry_id=watchlist_entry_id,
                label=label,
            )
        )
    logger.info(
        "Sighting: %s (%s) %s %s - matched %s",
        state.callsign or state.icao24,
        aircraft.typecode or "unknown type",
        EVENT_LOG_VERBS[event_type],
        airport.icao,
        [label for _, _, label in match_hits],
    )


@metrics.poll_duration_seconds.time()
def poll_job() -> None:
    with Session(engine) as session:
        watches = session.exec(select(AirportWatch)).all()
        if not watches:
            return

        airport_ids = list({w.airport_id for w in watches})
        airports = {a.id: a for a in session.exec(select(Airport).where(Airport.id.in_(airport_ids))).all()}

        # Poll each watched airport once, using the largest radius any of its
        # watchers asked for, instead of one OpenSky call per user.
        max_radius: dict[int, float] = {}
        for w in watches:
            max_radius[w.airport_id] = max(max_radius.get(w.airport_id, 0.0), w.radius_km)

        categories = session.exec(select(AircraftCategory)).all()
        watchlist_entries = session.exec(select(WatchlistEntry)).all()

        cutoff = datetime.utcnow() - timedelta(seconds=STALE_TRACK_SECONDS)
        for stale in session.exec(select(AircraftTrackState).where(AircraftTrackState.last_seen_at < cutoff)):
            session.delete(stale)
        session.commit()

        for airport_id, radius_km in max_radius.items():
            airport = airports.get(airport_id)
            if not airport:
                continue
            for state in flight_sources.fetch_merged_states(session, airport.lat, airport.lon, radius_km):
                _process_state(session, airport, state, categories, watchlist_entries)
        session.commit()


def notify_check_job() -> None:
    with Session(engine) as session:
        sightings = session.exec(select(Sighting).order_by(Sighting.landed_at.desc()).limit(200)).all()
        if not sightings:
            return

        sighting_ids = [s.id for s in sightings]
        matches_by_sighting: dict[int, list[SightingMatch]] = {}
        for m in session.exec(select(SightingMatch).where(SightingMatch.sighting_id.in_(sighting_ids))):
            matches_by_sighting.setdefault(m.sighting_id, []).append(m)

        airports = {a.id: a for a in session.exec(select(Airport))}

        for user in session.exec(select(User)):
            if not enabled_channels(session, user.id):
                continue
            watched_airport_ids = set(
                session.exec(select(AirportWatch.airport_id).where(AirportWatch.user_id == user.id))
            )
            if not watched_airport_ids:
                continue
            user_watchlist_ids = set(
                session.exec(select(WatchlistEntry.id).where(WatchlistEntry.user_id == user.id))
            )
            already_notified = set(
                session.exec(select(NotificationLog.sighting_id).where(NotificationLog.user_id == user.id))
            )

            if get_user_setting(session, user.id, "quiet_hours_enabled") == "true" and is_within_quiet_hours(
                datetime.utcnow(),
                get_user_setting(session, user.id, "quiet_hours_start"),
                get_user_setting(session, user.id, "quiet_hours_end"),
                get_user_setting(session, user.id, "quiet_hours_timezone"),
            ):
                # Skip entirely without touching NotificationLog - the
                # sighting stays "not yet notified" and goes out normally on
                # a later cycle once quiet hours end, instead of being lost.
                continue

            for sighting in sightings:
                if sighting.id in already_notified or sighting.airport_id not in watched_airport_ids:
                    continue
                relevant = [
                    m
                    for m in matches_by_sighting.get(sighting.id, [])
                    if (
                        m.category_key
                        and get_user_setting(session, user.id, f"category_enabled_{m.category_key}") == "true"
                    )
                    or (m.watchlist_entry_id and m.watchlist_entry_id in user_watchlist_ids)
                ]
                if not relevant:
                    continue

                airport = airports.get(sighting.airport_id)
                title = (
                    f"{EVENT_TITLE_PREFIXES[sighting.event_type]}: "
                    f"{sighting.typecode or 'Besonderes Flugzeug'} in {airport.icao if airport else '?'}"
                )
                event_phrase = EVENT_MESSAGE_PHRASES[sighting.event_type].format(
                    airport=airport.name if airport else sighting.airport_id
                )
                message = (
                    f"{sighting.callsign or sighting.icao24} "
                    f"({sighting.typecode or 'unbekannter Typ'}"
                    f"{', ' + sighting.operator if sighting.operator else ''}) {event_phrase}. "
                    f"Erkannt als: {', '.join(m.label for m in relevant)}"
                )
                if sighting.route_origin_icao or sighting.route_destination_icao:
                    message += (
                        f"\nRoute: {sighting.route_origin_icao or '?'} → "
                        f"{sighting.route_destination_icao or '?'}"
                    )
                results = notify_all(session, user.id, title, message, url=sighting.photo_link)
                if any(results.values()):
                    session.add(NotificationLog(user_id=user.id, sighting_id=sighting.id))
                    session.commit()
                    logger.info(
                        "Notified %s about %s at %s via %s",
                        user.email,
                        sighting.callsign or sighting.icao24,
                        airport.icao if airport else sighting.airport_id,
                        [c for c, ok in results.items() if ok],
                    )


def reschedule_poll_job(seconds: int) -> None:
    scheduler.reschedule_job("poll_job", trigger=IntervalTrigger(seconds=seconds))


def reschedule_backup_job(hours: int) -> None:
    scheduler.reschedule_job("backup_job", trigger=IntervalTrigger(hours=hours))


def start_scheduler() -> None:
    with Session(engine) as session:
        interval = int(get_setting(session, "poll_interval_seconds") or 90)
        backup_interval_hours = int(get_setting(session, "backup_interval_hours") or 24)

    scheduler.add_job(
        poll_job,
        trigger=IntervalTrigger(seconds=interval),
        id="poll_job",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        notify_check_job,
        trigger=IntervalTrigger(seconds=30),
        id="notify_check_job",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        aircraft_db.refresh_aircraft_db,
        trigger=IntervalTrigger(seconds=aircraft_db.REFRESH_INTERVAL_SECONDS),
        id="aircraft_db_refresh_job",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        backup.run_backup,
        trigger=IntervalTrigger(hours=backup_interval_hours),
        id="backup_job",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
