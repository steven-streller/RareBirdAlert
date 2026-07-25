import logging

import requests

from app.state_vector import StateVector

logger = logging.getLogger("rarebirdalert.adsblol")

BASE_URL = "https://api.adsb.lol/v2/point"
MAX_RADIUS_NM = 250

# dbFlags bitmask, adsb.lol/ADSBExchange convention.
DBFLAG_MILITARY = 1
DBFLAG_PIA = 2
DBFLAG_LADD = 4

_session = requests.Session()


def fetch_states(cfg: dict, lat: float, lon: float, radius_km: float) -> list[StateVector]:
    """Fetches live aircraft states within radius_km of (lat, lon) from adsb.lol.

    adsb.lol needs no authentication (cfg is accepted only for a uniform
    calling convention with app.opensky.fetch_states) and takes its radius in
    nautical miles, capped at 250. Returns an empty list on any request
    failure instead of raising, matching app.opensky.fetch_states.
    """
    radius_nm = max(1, min(MAX_RADIUS_NM, round(radius_km / 1.852)))
    url = f"{BASE_URL}/{lat}/{lon}/{radius_nm}"

    try:
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("adsb.lol request failed: %s", exc)
        return []

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
