from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class Airport(SQLModel, table=True):
    """A single airport, looked up from the bundled airport directory when a
    user adds it. Shared across all users - watching is done via AirportWatch."""

    id: Optional[int] = Field(default=None, primary_key=True)
    icao: str = Field(unique=True, index=True)
    iata: Optional[str] = None
    name: str
    lat: float
    lon: float
    municipality: Optional[str] = None
    country: Optional[str] = None


class AirportWatch(SQLModel, table=True):
    """A user subscribing to landings at an airport, with their own search radius."""

    __table_args__ = (UniqueConstraint("user_id", "airport_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    airport_id: int = Field(foreign_key="airport.id", index=True)
    radius_km: float = 15.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AircraftCategory(SQLModel, table=True):
    """Built-in, curated categories of "special" aircraft (military, Beluga, ...).

    Seeded globally on startup, like BaseAlert's Station list. Each user can
    toggle individual categories on/off via UserSetting (default: enabled).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)
    label: str
    description: Optional[str] = None
    match_type: str  # "typecode" | "callsign_prefix" | "operator_contains"
    pattern: str  # comma-separated list of patterns for this category


class WatchlistEntry(SQLModel, table=True):
    """A user-defined watch entry, e.g. one specific registration or type code."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    label: str
    match_type: str  # "typecode" | "registration" | "icao24" | "callsign_prefix" | "operator_contains"
    pattern: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AircraftTrackState(SQLModel, table=True):
    """Last known ground state per (aircraft, airport).

    Used to detect the airborne -> on-ground transition without re-triggering
    on every poll while the aircraft just sits parked at the gate.
    """

    __table_args__ = (UniqueConstraint("icao24", "airport_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    icao24: str = Field(index=True)
    airport_id: int = Field(foreign_key="airport.id", index=True)
    on_ground: bool = False
    last_seen_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AircraftMetadata(SQLModel, table=True):
    """Local cache of OpenSky's public aircraft database, keyed by icao24 hex."""

    icao24: str = Field(primary_key=True)
    registration: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    typecode: Optional[str] = None
    operator: Optional[str] = None
    icao_aircraft_type: Optional[str] = None
    category_description: Optional[str] = None


class Sighting(SQLModel, table=True):
    """A detected landing of a "special" aircraft at a watched airport.

    Created only once a landing has matched at least one category/watchlist
    entry - ordinary airline traffic never produces a row here.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    airport_id: int = Field(foreign_key="airport.id", index=True)
    icao24: str = Field(index=True)
    callsign: Optional[str] = None
    registration: Optional[str] = None
    typecode: Optional[str] = None
    operator: Optional[str] = None
    landed_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    # Best-effort route enrichment from adsbdb.com, looked up by callsign at
    # match time (see app/adsbdb.py) - None when the callsign is missing,
    # unknown to adsbdb, or the lookup failed.
    route_origin_icao: Optional[str] = None
    route_origin_name: Optional[str] = None
    route_destination_icao: Optional[str] = None
    route_destination_name: Optional[str] = None


class SightingMatch(SQLModel, table=True):
    """Why a Sighting was considered special - one row per matching category/entry.

    label is resolved and stored at match time so a Sighting's history still
    reads correctly even if a category or watchlist entry is later renamed
    or deleted.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    sighting_id: int = Field(foreign_key="sighting.id", index=True)
    category_key: Optional[str] = None
    watchlist_entry_id: Optional[int] = Field(default=None, foreign_key="watchlistentry.id")
    label: str


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    # The first account ever registered on an instance; controls global
    # infrastructure settings (poll interval, data sources) other users
    # can't touch. See db.py's init_db for how this is backfilled on
    # instances that predate this field.
    is_admin: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationLog(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "sighting_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sighting_id: int = Field(foreign_key="sighting.id", index=True)
    notified_at: datetime = Field(default_factory=datetime.utcnow)


class Setting(SQLModel, table=True):
    """Global settings shared by all users (poll interval, etc.)."""

    key: str = Field(primary_key=True)
    value: str


class UserSetting(SQLModel, table=True):
    """Per-user settings: notification channel config, category toggles, lead time."""

    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    key: str = Field(index=True)
    value: str
