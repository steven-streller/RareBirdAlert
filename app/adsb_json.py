"""Shared parsing for the readsb/tar1090-style JSON format used by several
community ADS-B networks (adsb.lol, airplanes.live, ...) - confirmed by
querying both APIs directly rather than assumed: same "ac" array, same
field names, same dbFlags bitmask convention.
"""

from app.state_vector import StateVector

DBFLAG_MILITARY = 1
DBFLAG_PIA = 2
DBFLAG_LADD = 4


def parse_aircraft_list(data: dict) -> list[StateVector]:
    states = []
    for item in data.get("ac") or []:
        icao24 = (item.get("hex") or "").strip()
        if not icao24:
            continue
        db_flags = item.get("dbFlags") or 0
        states.append(
            StateVector(
                icao24=icao24.lower(),
                callsign=(item.get("flight") or "").strip() or None,
                on_ground=item.get("alt_baro") == "ground",
                lat=item.get("lat"),
                lon=item.get("lon"),
                typecode=(item.get("t") or "").strip().upper() or None,
                registration=(item.get("r") or "").strip() or None,
                flagged_military=bool(db_flags & DBFLAG_MILITARY),
                flagged_pia=bool(db_flags & DBFLAG_PIA),
                flagged_ladd=bool(db_flags & DBFLAG_LADD),
            )
        )
    return states
