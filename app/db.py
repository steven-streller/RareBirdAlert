import csv
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AircraftCategory, Setting, User, UserSetting

DB_PATH = os.environ.get("RAREBIRDALERT_DB_PATH", "/app/data/rarebirdalert.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False, "timeout": 30})


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    # WAL lets readers (web requests) proceed while a writer (e.g. the
    # aircraft-db bulk import, which touches hundreds of thousands of rows)
    # holds a transaction open - without it, concurrent requests intermittently
    # fail with "database is locked". busy_timeout is a second line of
    # defense for the remaining brief writer-vs-writer contention.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()

AIRPORT_DIRECTORY_CSV = Path(__file__).parent / "data" / "airports.csv"

# Curated starting point, not an exhaustive/authoritative database - see
# docs/watchlist.md. Users extend coverage (e.g. BelugaXL by registration)
# with their own WatchlistEntry rows.
CATEGORIES = [
    {
        "key": "military",
        "label": "Militär",
        "description": (
            "Häufige militärische Callsign-Präfixe (u. a. Luftwaffe, USAF, RAF, "
            "französische/italienische Luftstreitkräfte, NATO). Erkennt nicht "
            "jeden Militärflug - manche fliegen unter zivilem Callsign."
        ),
        "match_type": "callsign_prefix",
        "pattern": (
            "GAF,GAM,RCH,REACH,RRR,ASCOT,NATO,NAF,CTM,FAF,IAM,CEFA,"
            "HOOK,DUKE,GRZ,BAF,CFC,VIVI"
        ),
    },
    {
        "key": "eurofighter_typhoon",
        "label": "Eurofighter Typhoon",
        "description": "Erkennt den Flugzeugtyp direkt über den ICAO-Typcode, unabhängig vom Callsign.",
        "match_type": "typecode",
        "pattern": "EUFI,EFA",
    },
    {
        "key": "heavy_lift_special",
        "label": "Spezial-Transporter",
        "description": (
            "Airbus Beluga (A300-600ST), Antonov An-124 und Lockheed C-5 Galaxy. "
            "Für die Beluga XL bitte eine eigene Watchlist-Eintragung per "
            "Kennung anlegen, siehe Dokumentation."
        ),
        "match_type": "typecode",
        "pattern": "A3ST,A124,C5,C5M",
    },
    {
        "key": "historic_classic",
        "label": "Historische Klassiker",
        "description": "Douglas DC-3/C-47, Boeing B-17 und B-29 - Oldtimer, die selten noch fliegen.",
        "match_type": "typecode",
        "pattern": "DC3,C47,B17,B29",
    },
    {
        "key": "adsb_flagged",
        "label": "Militär/Privat-ICAO (adsb.lol-Flag)",
        "description": (
            "Von adsb.lol als militärisch, PIA (Privacy ICAO Address) oder LADD "
            "(FAA-Liste versteckter ziviler Kennungen) markiert - zuverlässiger "
            "als die Callsign-Heuristik, aber nur verfügbar, wenn die Quelle "
            "„adsb.lol“ in den Einstellungen aktiviert ist."
        ),
        "match_type": "flagged_military_or_pia_or_ladd",
        "pattern": "",
    },
]

GLOBAL_DEFAULT_SETTINGS = {
    "poll_interval_seconds": "90",
    "source_enabled_opensky": "true",
    "source_enabled_adsblol": "true",
    "source_enabled_airplaneslive": "true",
    "opensky_client_id": "",
    "opensky_client_secret": "",
}

# Setting keys where a deploy-time env var takes precedence over whatever is
# stored in the DB - used for credentials, so a container secret can't be
# silently shadowed (or leaked into the DB) via the web UI.
ENV_OVERRIDABLE_SETTINGS = {
    "opensky_client_id": "OPENSKY_CLIENT_ID",
    "opensky_client_secret": "OPENSKY_CLIENT_SECRET",
}

USER_DEFAULT_SETTINGS = {
    # Pushover
    "pushover_enabled": "false",
    "pushover_user_key": "",
    "pushover_api_token": "",
    # ntfy
    "ntfy_enabled": "false",
    "ntfy_server_url": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_token": "",
    # Telegram
    "telegram_enabled": "false",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    # Discord
    "discord_enabled": "false",
    "discord_webhook_url": "",
    # Generic webhook
    "webhook_enabled": "false",
    "webhook_url": "",
    # Email
    "email_enabled": "false",
    "email_smtp_host": "",
    "email_smtp_port": "587",
    "email_smtp_user": "",
    "email_smtp_password": "",
    "email_from": "",
    "email_to": "",
    "email_use_tls": "true",
    # Quiet hours - notifications matched while inside this window are
    # neither sent nor logged, so they're delivered normally once it ends.
    "quiet_hours_enabled": "false",
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
    "quiet_hours_timezone": "Europe/Berlin",
}
# Every built-in category is enabled by default so a fresh account gets
# alerts immediately without having to configure anything first.
USER_DEFAULT_SETTINGS.update({f"category_enabled_{c['key']}": "true" for c in CATEGORIES})


def _ensure_user_is_admin_column() -> None:
    """SQLModel.metadata.create_all only creates tables that don't exist yet
    - it never alters an existing one. Instances that predate the is_admin
    field have a `user` table without it, so add it by hand the one time
    it's missing. There's no Alembic here (deliberately, for a project this
    size), so this is the lightest migration that still works.
    """
    with engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()}
        if columns and "is_admin" not in columns:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
            conn.commit()


def _ensure_sighting_route_columns() -> None:
    """Same rationale as _ensure_user_is_admin_column - the route_* columns
    on Sighting (adsbdb.com enrichment) were added after the initial release,
    so existing databases need them backfilled by hand.
    """
    with engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sighting)").fetchall()}
        if not columns:
            return
        for column in (
            "route_origin_icao",
            "route_origin_name",
            "route_destination_icao",
            "route_destination_name",
        ):
            if column not in columns:
                conn.exec_driver_sql(f"ALTER TABLE sighting ADD COLUMN {column} TEXT")
        conn.commit()


def _ensure_sighting_photo_columns() -> None:
    """Same rationale as _ensure_sighting_route_columns - the photo_* columns
    on Sighting (planespotters.net enrichment) were added after the initial
    release, so existing databases need them backfilled by hand.
    """
    with engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sighting)").fetchall()}
        if not columns:
            return
        for column in ("photo_thumbnail_url", "photo_large_url", "photo_link"):
            if column not in columns:
                conn.exec_driver_sql(f"ALTER TABLE sighting ADD COLUMN {column} TEXT")
        conn.commit()


def _ensure_admin_exists(session: Session) -> None:
    """Guarantees exactly one admin exists after every startup.

    Covers two cases: a brand-new instance (the first /register call sets
    is_admin itself, so this is a no-op) and an instance upgraded from
    before is_admin existed, where the migration above just added the column
    defaulting everyone to False - here the earliest-created account is
    promoted so the admin pages aren't locked out for everybody.
    """
    if session.exec(select(User).where(User.is_admin)).first():
        return
    first_user = session.exec(select(User).order_by(User.id)).first()
    if first_user:
        first_user.is_admin = True
        session.add(first_user)


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _ensure_user_is_admin_column()
    _ensure_sighting_route_columns()
    _ensure_sighting_photo_columns()
    with Session(engine) as session:
        for category in CATEGORIES:
            existing = session.exec(
                select(AircraftCategory).where(AircraftCategory.key == category["key"])
            ).first()
            if not existing:
                session.add(AircraftCategory(**category))
        for key, value in GLOBAL_DEFAULT_SETTINGS.items():
            existing = session.exec(select(Setting).where(Setting.key == key)).first()
            if not existing:
                session.add(Setting(key=key, value=value))
        _ensure_admin_exists(session)
        session.commit()


def get_setting(session: Session, key: str) -> str:
    setting = session.exec(select(Setting).where(Setting.key == key)).first()
    return setting.value if setting else GLOBAL_DEFAULT_SETTINGS.get(key, "")


def get_effective_setting(session: Session, key: str) -> tuple[str, bool]:
    """Like get_setting, but for keys in ENV_OVERRIDABLE_SETTINGS an env var
    wins when set. Returns (value, is_env_locked) - the settings UI uses the
    second value to render the field read-only instead of silently letting a
    saved change have no effect.
    """
    env_var = ENV_OVERRIDABLE_SETTINGS.get(key)
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            return env_value, True
    return get_setting(session, key), False


def set_setting(session: Session, key: str, value: str) -> None:
    setting = session.exec(select(Setting).where(Setting.key == key)).first()
    if setting:
        setting.value = value
        session.add(setting)
    else:
        session.add(Setting(key=key, value=value))
    session.commit()


def get_user_setting(session: Session, user_id: int, key: str) -> str:
    setting = session.exec(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    ).first()
    return setting.value if setting else USER_DEFAULT_SETTINGS.get(key, "")


def set_user_setting(session: Session, user_id: int, key: str, value: str) -> None:
    setting = session.exec(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    ).first()
    if setting:
        setting.value = value
        session.add(setting)
    else:
        session.add(UserSetting(user_id=user_id, key=key, value=value))
    session.commit()


# --- Bundled airport directory (ICAO -> name/coordinates lookup) --------------
# Loaded once from app/data/airports.csv (derived from the public-domain
# OurAirports dataset) so adding an airport never needs an external API call.


@lru_cache(maxsize=1)
def _load_airport_directory() -> dict[str, dict]:
    directory: dict[str, dict] = {}
    with open(AIRPORT_DIRECTORY_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            directory[row["icao"]] = {
                "icao": row["icao"],
                "iata": row["iata"] or None,
                "name": row["name"],
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "municipality": row["municipality"] or None,
                "country": row["country"] or None,
            }
    return directory


def lookup_airport_directory(icao: str) -> dict | None:
    return _load_airport_directory().get(icao.strip().upper())


def search_airport_directory(query: str, limit: int = 20) -> list[dict]:
    query = query.strip().upper()
    if len(query) < 2:
        return []
    results = []
    for entry in _load_airport_directory().values():
        if (
            entry["icao"].startswith(query)
            or (entry["iata"] and entry["iata"] == query)
            or query in entry["name"].upper()
        ):
            results.append(entry)
            if len(results) >= limit:
                break
    return results
