import logging
import os
import secrets
from datetime import date, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app import flight_sources
from app.auth import get_current_user, login_user, logout_user, require_admin, require_user
from app.csrf import get_or_create_csrf_token, verify_csrf
from app.db import (
    engine,
    get_effective_setting,
    get_setting,
    get_user_setting,
    init_db,
    lookup_airport_directory,
    search_airport_directory,
    set_setting,
    set_user_setting,
)
from app.matcher import MATCH_TYPES
from app.models import (
    AircraftCategory,
    Airport,
    AirportWatch,
    Sighting,
    SightingMatch,
    User,
    WatchlistEntry,
)
from app.notifications import CHANNELS, send_to_channel
from app.scheduler import poll_job, reschedule_poll_job, start_scheduler
from app.security import hash_password, verify_password
from app.version import __version__

logging.basicConfig(level=logging.INFO)


class _HealthCheckLogFilter(logging.Filter):
    """Keeps k8s readiness/liveness probe hits out of the access log - they'd
    otherwise drown out everything else every few seconds."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HealthCheckLogFilter())

# APScheduler logs "Running job ..." / "... executed successfully" at INFO
# for every single run - with notify_check_job firing every 30s that adds up
# fast. Failures still come through at ERROR.
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

app = FastAPI(title="RareBirdAlert")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY") or secrets.token_hex(32),
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_version"] = __version__
templates.env.globals["csrf_token"] = get_or_create_csrf_token

REGISTRATION_ENABLED = os.environ.get("REGISTRATION_ENABLED", "true").lower() != "false"

MATCH_TYPE_LABELS = {
    "typecode": "Flugzeugtyp",
    "registration": "Kennung",
    "icao24": "ICAO24-Hexcode",
    "callsign_prefix": "Callsign-Präfix",
    "operator_contains": "Betreiber enthält",
}


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    start_scheduler()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- Auth -------------------------------------------------------------------


@app.get("/register")
def register_page(request: Request, error: str = ""):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"error": error, "registration_enabled": REGISTRATION_ENABLED},
    )


@app.post("/register")
async def register(request: Request):
    if not REGISTRATION_ENABLED:
        return RedirectResponse(url="/register", status_code=303)

    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    if not email or "@" not in email:
        return RedirectResponse(url="/register?error=email", status_code=303)
    if len(password) < 8:
        return RedirectResponse(url="/register?error=password_length", status_code=303)
    if password != password_confirm:
        return RedirectResponse(url="/register?error=password_mismatch", status_code=303)

    with Session(engine) as session:
        if session.exec(select(User).where(User.email == email)).first():
            return RedirectResponse(url="/register?error=taken", status_code=303)
        is_first_user = session.exec(select(User)).first() is None
        user = User(email=email, password_hash=hash_password(password), is_admin=is_first_user)
        session.add(user)
        session.commit()
        session.refresh(user)

    login_user(request, user)
    return RedirectResponse(url="/", status_code=303)


@app.get("/login")
def login_page(request: Request, error: str = ""):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "registration_enabled": REGISTRATION_ENABLED},
    )


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()

    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse(url="/login?error=1", status_code=303)

    login_user(request, user)
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)


# --- Dashboard ----------------------------------------------------------------

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _day_label(day: date, today: date) -> str:
    delta = (today - day).days
    if delta == 0:
        return "Heute"
    if delta == 1:
        return "Gestern"
    return WEEKDAYS_DE[day.weekday()]


@app.get("/")
def dashboard(request: Request, current_user: User = Depends(require_user)):
    with Session(engine) as session:
        today = datetime.now().date()
        watch_airport_ids = session.exec(
            select(AirportWatch.airport_id).where(AirportWatch.user_id == current_user.id)
        ).all()
        has_airports = bool(watch_airport_ids)

        day_groups: list[tuple[str, list[dict]]] = []
        if watch_airport_ids:
            sightings = session.exec(
                select(Sighting)
                .where(Sighting.airport_id.in_(watch_airport_ids))
                .order_by(Sighting.landed_at.desc())
                .limit(100)
            ).all()
            airports = {a.id: a for a in session.exec(select(Airport).where(Airport.id.in_(watch_airport_ids)))}

            matches_by_sighting: dict[int, list[str]] = {}
            sighting_ids = [s.id for s in sightings]
            if sighting_ids:
                for m in session.exec(select(SightingMatch).where(SightingMatch.sighting_id.in_(sighting_ids))):
                    matches_by_sighting.setdefault(m.sighting_id, []).append(m.label)

            for sighting in sightings:
                day_label = _day_label(sighting.landed_at.date(), today)
                if not day_groups or day_groups[-1][0] != day_label:
                    day_groups.append((day_label, []))
                day_groups[-1][1].append(
                    {
                        "sighting": sighting,
                        "airport": airports.get(sighting.airport_id),
                        "matches": matches_by_sighting.get(sighting.id, []),
                    }
                )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "current_user": current_user,
            "has_airports": has_airports,
            "day_groups": day_groups,
        },
    )


# --- Airports -------------------------------------------------------------------


@app.get("/airports")
def airports_page(
    request: Request, error: str = "", added: str = "", current_user: User = Depends(require_user)
):
    with Session(engine) as session:
        watch_rows = session.exec(select(AirportWatch).where(AirportWatch.user_id == current_user.id)).all()
        airport_ids = [w.airport_id for w in watch_rows]
        airports = (
            {a.id: a for a in session.exec(select(Airport).where(Airport.id.in_(airport_ids)))}
            if airport_ids
            else {}
        )
        watches = sorted(
            (
                {"watch": w, "airport": airports[w.airport_id]}
                for w in watch_rows
                if w.airport_id in airports
            ),
            key=lambda row: row["airport"].icao,
        )
    return templates.TemplateResponse(
        request,
        "airports.html",
        {
            "active": "airports",
            "current_user": current_user,
            "watches": watches,
            "error": error,
            "flash": "Flughafen hinzugefügt." if added else None,
        },
    )


@app.get("/airports/search")
def airports_search(request: Request, q: str = "", current_user: User = Depends(require_user)):
    results = search_airport_directory(q) if q else []
    return templates.TemplateResponse(request, "_airport_search_results.html", {"results": results, "query": q})


@app.post("/airports")
async def add_airport(request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    icao = str(form.get("icao", "")).strip().upper()
    try:
        radius_km = max(1.0, min(200.0, float(form.get("radius_km") or 15)))
    except ValueError:
        radius_km = 15.0

    entry = lookup_airport_directory(icao)
    if not entry:
        return RedirectResponse(url="/airports?error=notfound", status_code=303)

    with Session(engine) as session:
        airport = session.exec(select(Airport).where(Airport.icao == icao)).first()
        if not airport:
            airport = Airport(**entry)
            session.add(airport)
            session.commit()
            session.refresh(airport)

        watch = session.exec(
            select(AirportWatch).where(
                AirportWatch.user_id == current_user.id, AirportWatch.airport_id == airport.id
            )
        ).first()
        if watch:
            watch.radius_km = radius_km
            session.add(watch)
        else:
            session.add(AirportWatch(user_id=current_user.id, airport_id=airport.id, radius_km=radius_km))
        session.commit()

    return RedirectResponse(url="/airports?added=1", status_code=303)


@app.post("/airports/{watch_id}/delete")
async def delete_airport(watch_id: int, request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    with Session(engine) as session:
        watch = session.get(AirportWatch, watch_id)
        if watch and watch.user_id == current_user.id:
            session.delete(watch)
            session.commit()
    return RedirectResponse(url="/airports", status_code=303)


# --- Map ------------------------------------------------------------------------


@app.get("/map")
def map_page(request: Request, current_user: User = Depends(require_user)):
    with Session(engine) as session:
        watch_rows = session.exec(select(AirportWatch).where(AirportWatch.user_id == current_user.id)).all()
        airport_ids = [w.airport_id for w in watch_rows]
        airports = (
            {a.id: a for a in session.exec(select(Airport).where(Airport.id.in_(airport_ids)))}
            if airport_ids
            else {}
        )
        watches = [
            {
                "icao": airports[w.airport_id].icao,
                "name": airports[w.airport_id].name,
                "lat": airports[w.airport_id].lat,
                "lon": airports[w.airport_id].lon,
                "radius_km": w.radius_km,
            }
            for w in watch_rows
            if w.airport_id in airports
        ]

    return templates.TemplateResponse(
        request,
        "map.html",
        {
            "active": "map",
            "current_user": current_user,
            "watches": watches,
        },
    )


@app.get("/map/live")
def map_live_aircraft(current_user: User = Depends(require_user)):
    """Fetches aircraft near the current user's watched airports live, on
    demand - not from the scheduler's cadence - so a click on the map page
    directly demonstrates whether the enabled data sources are returning
    anything for that spot right now."""
    with Session(engine) as session:
        watches = session.exec(select(AirportWatch).where(AirportWatch.user_id == current_user.id)).all()
        if not watches:
            return {"aircraft": []}

        airport_ids = list({w.airport_id for w in watches})
        airports = {a.id: a for a in session.exec(select(Airport).where(Airport.id.in_(airport_ids)))}

        max_radius: dict[int, float] = {}
        for w in watches:
            max_radius[w.airport_id] = max(max_radius.get(w.airport_id, 0.0), w.radius_km)

        aircraft = []
        for airport_id, radius_km in max_radius.items():
            airport = airports.get(airport_id)
            if not airport:
                continue
            for state in flight_sources.fetch_merged_states(session, airport.lat, airport.lon, radius_km):
                if state.lat is None or state.lon is None:
                    continue
                aircraft.append(
                    {
                        "icao24": state.icao24,
                        "callsign": state.callsign,
                        "lat": state.lat,
                        "lon": state.lon,
                        "on_ground": state.on_ground,
                        "typecode": state.typecode,
                        "registration": state.registration,
                        "flagged_military": state.flagged_military,
                        "flagged_pia": state.flagged_pia,
                        "flagged_ladd": state.flagged_ladd,
                        "airport_icao": airport.icao,
                    }
                )

    return {"aircraft": aircraft}


# --- Watchlist ------------------------------------------------------------------


@app.get("/watchlist")
def watchlist_page(request: Request, saved: str = "", current_user: User = Depends(require_user)):
    with Session(engine) as session:
        categories = session.exec(select(AircraftCategory)).all()
        category_rows = [
            {
                "key": c.key,
                "label": c.label,
                "description": c.description,
                "enabled": get_user_setting(session, current_user.id, f"category_enabled_{c.key}") == "true",
            }
            for c in categories
        ]
        entries = session.exec(select(WatchlistEntry).where(WatchlistEntry.user_id == current_user.id)).all()
    return templates.TemplateResponse(
        request,
        "watchlist.html",
        {
            "active": "watchlist",
            "current_user": current_user,
            "categories": category_rows,
            "entries": entries,
            "match_type_labels": MATCH_TYPE_LABELS,
            "flash": "Gespeichert." if saved else None,
        },
    )


@app.post("/watchlist/category/{key}/toggle")
async def toggle_category(key: str, request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    with Session(engine) as session:
        category = session.exec(select(AircraftCategory).where(AircraftCategory.key == key)).first()
        if category:
            currently_enabled = get_user_setting(session, current_user.id, f"category_enabled_{key}") == "true"
            set_user_setting(
                session, current_user.id, f"category_enabled_{key}", "false" if currently_enabled else "true"
            )
    return RedirectResponse(url="/watchlist", status_code=303)


@app.post("/watchlist")
async def add_watchlist_entry(request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    label = str(form.get("label", "")).strip()
    match_type = str(form.get("match_type", "")).strip()
    pattern = str(form.get("pattern", "")).strip()

    if not label or match_type not in MATCH_TYPES or not pattern:
        return RedirectResponse(url="/watchlist", status_code=303)

    with Session(engine) as session:
        session.add(WatchlistEntry(user_id=current_user.id, label=label, match_type=match_type, pattern=pattern))
        session.commit()

    return RedirectResponse(url="/watchlist?saved=1", status_code=303)


@app.post("/watchlist/{entry_id}/delete")
async def delete_watchlist_entry(entry_id: int, request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    with Session(engine) as session:
        entry = session.get(WatchlistEntry, entry_id)
        if entry and entry.user_id == current_user.id:
            session.delete(entry)
            session.commit()
    return RedirectResponse(url="/watchlist", status_code=303)


# --- Settings (personal notification channels) --------------------------------

# checkbox settings keys that are absent from form data when unchecked
CHANNEL_CHECKBOX_FIELDS = {
    field[0] for channel in CHANNELS.values() for field in channel["fields"] if field[2] == "checkbox"
}
CHANNEL_TEXT_KEYS = [key for channel in CHANNELS.values() for key in channel["keys"]]


def _safe_channel_anchor(value: str) -> str:
    """Map arbitrary input onto a known-safe literal for use in a redirect URL/anchor.

    Returns one of the CHANNELS keys, never the input itself - even on a
    match, the returned string comes from the fixed CHANNELS collection, not
    from `value` - so static analysis (and a would-be attacker) can't treat
    the redirect target as carrying attacker-controlled data (CWE-601).
    """
    for allowed in CHANNELS:
        if allowed == value:
            return allowed
    return "general"


@app.get("/settings")
def settings_page(
    request: Request,
    saved: str = "",
    tested: str = "",
    current_user: User = Depends(require_user),
):
    with Session(engine) as session:
        settings = {}
        for key in list(CHANNEL_CHECKBOX_FIELDS) + [f"{c}_enabled" for c in CHANNELS] + CHANNEL_TEXT_KEYS:
            settings[key] = get_user_setting(session, current_user.id, key)

    flash = None
    if saved:
        label = CHANNELS[saved]["label"] if saved in CHANNELS else "Einstellungen"
        flash = f"„{label}“ gespeichert."
    elif tested == "ok":
        flash = "Test-Benachrichtigung gesendet."
    elif tested == "fail":
        flash = "Test-Benachrichtigung fehlgeschlagen – prüfe die Zugangsdaten und die Logs."

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "current_user": current_user,
            "settings": settings,
            "channels": CHANNELS,
            "flash": flash,
        },
    )


@app.post("/settings")
async def save_settings(request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    section = form.get("_section", "")

    if section in CHANNELS:
        with Session(engine) as session:
            set_user_setting(
                session,
                current_user.id,
                f"{section}_enabled",
                "true" if form.get(f"{section}_enabled") else "false",
            )
            for key in CHANNELS[section]["keys"]:
                if key in CHANNEL_CHECKBOX_FIELDS:
                    set_user_setting(session, current_user.id, key, "true" if form.get(key) else "false")
                else:
                    set_user_setting(session, current_user.id, key, str(form.get(key, "")).strip())

    anchor = _safe_channel_anchor(section)
    return RedirectResponse(url=f"/settings?saved={anchor}#{anchor}", status_code=303)


@app.post("/settings/test/{channel}")
async def test_notification(channel: str, request: Request, current_user: User = Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    if channel not in CHANNELS:
        return RedirectResponse(url="/settings?tested=fail", status_code=303)
    with Session(engine) as session:
        ok = send_to_channel(
            session, current_user.id, channel, "RareBirdAlert Test", "Testbenachrichtigung von RareBirdAlert 🛩️"
        )
    anchor = _safe_channel_anchor(channel)
    return RedirectResponse(url=f"/settings?tested={'ok' if ok else 'fail'}#{anchor}", status_code=303)


# --- Admin (global infrastructure, admin account only) -------------------------

SOURCE_ANCHORS = tuple(f"source_{key}" for key in flight_sources.SOURCES)
ALLOWED_ADMIN_ANCHORS = ("general", *SOURCE_ANCHORS)


def _safe_admin_anchor(value: str) -> str:
    """Same CWE-601 concern as _safe_channel_anchor above, for the admin page."""
    for allowed in ALLOWED_ADMIN_ANCHORS:
        if allowed == value:
            return allowed
    return "general"


@app.get("/admin")
def admin_page(
    request: Request,
    saved: str = "",
    polled: str = "",
    current_user: User = Depends(require_admin),
):
    with Session(engine) as session:
        settings = {"poll_interval_seconds": get_setting(session, "poll_interval_seconds")}

        source_settings = {}
        for key, source in flight_sources.SOURCES.items():
            source_settings[f"source_enabled_{key}"] = get_setting(session, f"source_enabled_{key}")
            for field_key in source["keys"]:
                value, locked = get_effective_setting(session, field_key)
                source_settings[field_key] = value
                source_settings[f"{field_key}__locked"] = locked

    flash = None
    if saved:
        if saved.removeprefix("source_") in flight_sources.SOURCES:
            label = flight_sources.SOURCES[saved.removeprefix("source_")]["label"]
        else:
            label = "Allgemein"
        flash = f"„{label}“ gespeichert."
    elif polled:
        flash = "Poll-Zyklus manuell angestoßen."

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "active": "admin",
            "current_user": current_user,
            "settings": settings,
            "sources": flight_sources.SOURCES,
            "source_settings": source_settings,
            "flash": flash,
        },
    )


@app.post("/admin")
async def save_admin_settings(request: Request, current_user: User = Depends(require_admin)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    section = form.get("_section", "general")

    with Session(engine) as session:
        if section == "general":
            poll_interval = max(30, int(form.get("poll_interval_seconds") or 90))
            set_setting(session, "poll_interval_seconds", str(poll_interval))
            reschedule_poll_job(poll_interval)
        elif section.removeprefix("source_") in flight_sources.SOURCES:
            key = section.removeprefix("source_")
            set_setting(session, f"source_enabled_{key}", "true" if form.get("source_enabled") else "false")
            for field_key in flight_sources.SOURCES[key]["keys"]:
                _current_value, locked = get_effective_setting(session, field_key)
                if not locked:
                    set_setting(session, field_key, str(form.get(field_key, "")).strip())

    anchor = _safe_admin_anchor(section)
    return RedirectResponse(url=f"/admin?saved={anchor}#{anchor}", status_code=303)


@app.post("/poll-now")
async def poll_now(request: Request, current_user: User = Depends(require_admin)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    poll_job()
    return RedirectResponse(url="/admin?polled=1", status_code=303)
