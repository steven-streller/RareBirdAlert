import logging
from urllib.parse import quote

import requests

logger = logging.getLogger("rarebirdalert.adsbdb")

CALLSIGN_URL = "https://api.adsbdb.com/v0/callsign"


def fetch_route(callsign: str) -> dict | None:
    """Looks up the origin/destination airport for a callsign via adsbdb.com -
    a free, unauthenticated, crowd-sourced route database. This is schedule
    data keyed by callsign, not a live position, so it's a best-effort
    enrichment for notifications/dashboard rather than something to alert on
    directly. Returns None on anything short of a clean match (unknown
    callsign, timeout, malformed response) - callers must treat the lookup as
    optional and never let it block a sighting from being recorded.
    """
    callsign = (callsign or "").strip()
    if not callsign:
        return None

    try:
        resp = requests.get(f"{CALLSIGN_URL}/{quote(callsign)}", timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("adsbdb route lookup failed for %s: %s", callsign, exc)
        return None

    flightroute = (data.get("response") or {})
    if not isinstance(flightroute, dict):
        return None
    flightroute = flightroute.get("flightroute")
    if not isinstance(flightroute, dict):
        return None

    origin = flightroute.get("origin") or {}
    destination = flightroute.get("destination") or {}
    return {
        "origin_icao": origin.get("icao_code"),
        "origin_name": origin.get("name"),
        "destination_icao": destination.get("icao_code"),
        "destination_name": destination.get("name"),
    }
