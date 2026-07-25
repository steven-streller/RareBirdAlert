import logging

import requests

from app import adsb_json
from app.state_vector import StateVector

logger = logging.getLogger("rarebirdalert.airplaneslive")

BASE_URL = "https://api.airplanes.live/v2/point"
MAX_RADIUS_NM = 250

_session = requests.Session()


def fetch_states(cfg: dict, lat: float, lon: float, radius_km: float) -> list[StateVector]:
    """Fetches live aircraft states within radius_km of (lat, lon) from airplanes.live.

    Same readsb/tar1090-style JSON as adsb.lol (see app/adsb_json.py) and no
    authentication needed either, but airplanes.live documents an explicit
    1 request/second rate limit - keep the poll interval high enough that
    this source isn't hit faster than that across all watched airports.
    Returns an empty list on any request failure instead of raising,
    matching the other source clients.
    """
    radius_nm = max(1, min(MAX_RADIUS_NM, round(radius_km / 1.852)))
    url = f"{BASE_URL}/{lat}/{lon}/{radius_nm}"

    try:
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("airplanes.live request failed: %s", exc)
        return []

    return adsb_json.parse_aircraft_list(data)
