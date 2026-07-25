import logging

import requests

from app.version import __version__

logger = logging.getLogger("rarebirdalert.planespotters")

PHOTOS_URL = "https://api.planespotters.net/pub/photos/hex"
# planespotters.net rejects generic library User-Agents (e.g. "python-requests/x.x")
# with a 403 - confirmed against the live API, not assumed. A descriptive one
# identifying the app and a contact URL is required, per their published API guide.
USER_AGENT = f"RareBirdAlert/{__version__} (+https://github.com/steven-streller/RareBirdAlert)"


def fetch_photo(icao24: str) -> dict | None:
    """Looks up a spotter photo for an aircraft by its ICAO24 hex via
    planespotters.net - a free, unauthenticated (but User-Agent-gated) photo
    database. Best-effort enrichment: returns None on anything short of a
    clean match with at least one photo (unknown aircraft, timeout,
    malformed response) - callers must treat this as optional and never let
    it block a sighting from being recorded.
    """
    icao24 = (icao24 or "").strip()
    if not icao24:
        return None

    try:
        resp = requests.get(f"{PHOTOS_URL}/{icao24}", headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("planespotters.net photo lookup failed for %s: %s", icao24, exc)
        return None

    photos = data.get("photos")
    if not isinstance(photos, list) or not photos:
        return None
    photo = photos[0]
    if not isinstance(photo, dict):
        return None

    thumbnail = photo.get("thumbnail") or {}
    thumbnail_large = photo.get("thumbnail_large") or {}
    return {
        "thumbnail_url": thumbnail.get("src"),
        "large_url": thumbnail_large.get("src"),
        "link": photo.get("link"),
    }
