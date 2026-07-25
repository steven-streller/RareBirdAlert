import logging
import math
import os
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger("rarebirdalert.opensky")

STATES_URL = "https://opensky-network.org/api/states/all"
# Ruff's hardcoded-password check (S105) fires on the literal "token" in this
# OAuth2 endpoint URL below - suppressed there, it's a URL, not a secret.
TOKEN_ENDPOINT = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"  # noqa: S105
)

_session = requests.Session()
_token_cache: dict[str, float | str] = {"access_token": "", "expires_at": 0.0}


@dataclass
class StateVector:
    icao24: str
    callsign: str | None
    on_ground: bool
    lat: float | None
    lon: float | None


def _bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Returns (lamin, lomin, lamax, lomax) around a point for a given radius."""
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - lat_delta, lon - lon_delta, lat + lat_delta, lon + lon_delta)


def _get_access_token() -> str | None:
    client_id = os.environ.get("OPENSKY_CLIENT_ID")
    client_secret = os.environ.get("OPENSKY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    if _token_cache["access_token"] and time.time() < float(_token_cache["expires_at"]):
        return str(_token_cache["access_token"])

    try:
        resp = _session.post(
            TOKEN_ENDPOINT,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("OpenSky OAuth2 token request failed: %s", exc)
        return None

    _token_cache["access_token"] = data["access_token"]
    # Refresh a bit early to avoid a request failing right at expiry.
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 1800)) - 30
    return str(_token_cache["access_token"])


def fetch_states(lat: float, lon: float, radius_km: float) -> list[StateVector]:
    """Fetches live aircraft states within radius_km of (lat, lon).

    Returns an empty list on any request failure (rate limit, timeout, ...)
    instead of raising - a poll cycle skipping one airport is not fatal and
    will simply retry on the next scheduled run.
    """
    lamin, lomin, lamax, lomax = _bounding_box(lat, lon, radius_km)
    params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
    headers = {}
    token = _get_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = _session.get(STATES_URL, params=params, headers=headers, timeout=15)
        if resp.status_code == 429:
            logger.warning("OpenSky rate limit hit, skipping this poll")
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("OpenSky states request failed: %s", exc)
        return []

    states = []
    for row in data.get("states") or []:
        icao24 = row[0]
        if not icao24:
            continue
        callsign = (row[1] or "").strip() or None
        states.append(
            StateVector(
                icao24=icao24.strip().lower(),
                callsign=callsign,
                on_ground=bool(row[8]),
                lat=row[6],
                lon=row[5],
            )
        )
    return states
