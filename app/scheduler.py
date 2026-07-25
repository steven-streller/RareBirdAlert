import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from app import adsbdb, aircraft_db, flight_sources, metrics, planespotters
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
    landed_event = state.on_ground and not was_on_ground

    if track:
        track.on_ground = state.on_ground
        track.last_seen_at = datetime.utcnow()
        session.add(track)
    else:
        session.add(AircraftTrackState(icao24=state.icao24, airport_id=airport.id, on_ground=state.on_ground))

    if not landed_event:
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
    photo = planespotters.fetch_photo(state.icao24)

    sighting = Sighting(
        airport_id=airport.id,
        icao24=state.icao24,
        callsign=state.callsign,
        registration=aircraft.registration,
        typecode=aircraft.typecode,
        operator=aircraft.operator,
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
        "Sighting: %s (%s) landed at %s - matched %s",
        state.callsign or state.icao24,
        aircraft.typecode or "unknown type",
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
                title = f"{sighting.typecode or 'Besonderes Flugzeug'} in {airport.icao if airport else '?'}"
                message = (
                    f"{sighting.callsign or sighting.icao24} "
                    f"({sighting.typecode or 'unbekannter Typ'}"
                    f"{', ' + sighting.operator if sighting.operator else ''}) ist in "
                    f"{airport.name if airport else sighting.airport_id} gelandet. "
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


def start_scheduler() -> None:
    with Session(engine) as session:
        interval = int(get_setting(session, "poll_interval_seconds") or 90)

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
    scheduler.start()
