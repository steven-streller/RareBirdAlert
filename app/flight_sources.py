from sqlmodel import Session

from app import adsblol, airplaneslive, opensky
from app.db import get_effective_setting, get_setting
from app.state_vector import StateVector

# Each source declares its settings fields the same way app/notifications.py's
# CHANNELS does: (key, label, input_type, placeholder). The settings page
# renders these generically. Sources are global (not per-user) - airports and
# polling are already shared infrastructure, unlike notification channels.
SOURCES = {
    "opensky": {
        "label": "OpenSky Network",
        "fetch": opensky.fetch_states,
        "fields": [
            ("opensky_client_id", "Client ID", "text", None),
            ("opensky_client_secret", "Client Secret", "password", None),
        ],
    },
    "adsblol": {
        "label": "adsb.lol",
        "fetch": adsblol.fetch_states,
        "fields": [],
    },
    "airplaneslive": {
        "label": "airplanes.live",
        "fetch": airplaneslive.fetch_states,
        "fields": [],
    },
}
for _source in SOURCES.values():
    _source["keys"] = [field[0] for field in _source["fields"]]


def _source_config(session: Session, key: str) -> dict:
    cfg = {}
    for field_key in SOURCES[key]["keys"]:
        value, _locked = get_effective_setting(session, field_key)
        cfg[field_key] = value
    return cfg


def enabled_sources(session: Session) -> list[str]:
    return [key for key in SOURCES if get_setting(session, f"source_enabled_{key}") == "true"]


def _merge(a: StateVector, b: StateVector) -> StateVector:
    return StateVector(
        icao24=a.icao24,
        callsign=a.callsign or b.callsign,
        # A fresh "on the ground" from one source shouldn't be suppressed by
        # a possibly-lagging "still airborne" from another.
        on_ground=a.on_ground or b.on_ground,
        lat=a.lat if a.lat is not None else b.lat,
        lon=a.lon if a.lon is not None else b.lon,
        typecode=a.typecode or b.typecode,
        registration=a.registration or b.registration,
        flagged_military=a.flagged_military or b.flagged_military,
        flagged_pia=a.flagged_pia or b.flagged_pia,
        flagged_ladd=a.flagged_ladd or b.flagged_ladd,
        # is-not-None fallback, not `or` - 0 kt / 0 fpm is a valid reading.
        ground_speed_kt=a.ground_speed_kt if a.ground_speed_kt is not None else b.ground_speed_kt,
        vertical_rate_fpm=a.vertical_rate_fpm if a.vertical_rate_fpm is not None else b.vertical_rate_fpm,
    )


def fetch_merged_states(session: Session, lat: float, lon: float, radius_km: float) -> list[StateVector]:
    """Queries every enabled source and merges results by icao24.

    When the same aircraft is seen by multiple sources, fields are combined
    rather than one source's result overwriting the other's - e.g. OpenSky
    (no live type/registration) doesn't blank out values adsb.lol already
    supplied for the same aircraft.
    """
    merged: dict[str, StateVector] = {}
    for key in enabled_sources(session):
        cfg = _source_config(session, key)
        for state in SOURCES[key]["fetch"](cfg, lat, lon, radius_km):
            existing = merged.get(state.icao24)
            merged[state.icao24] = state if existing is None else _merge(existing, state)
    return list(merged.values())
