"""Pure aircraft/category matching logic - no DB or network access, so it's
cheap to unit test in isolation from the scheduler and OpenSky client."""

from dataclasses import dataclass

MATCH_TYPES = (
    "typecode",
    "registration",
    "icao24",
    "callsign_prefix",
    "operator_contains",
    "flagged_military_or_pia_or_ladd",
)


@dataclass
class AircraftInfo:
    icao24: str
    callsign: str | None = None
    registration: str | None = None
    typecode: str | None = None
    operator: str | None = None
    # Source-reported flags (see app/state_vector.py) - only adsb.lol sets
    # these today. Independent of `pattern`, unlike every other match_type.
    flagged_military: bool = False
    flagged_pia: bool = False
    flagged_ladd: bool = False


def _patterns(pattern: str) -> list[str]:
    return [p.strip().upper() for p in pattern.split(",") if p.strip()]


def matches(match_type: str, pattern: str, aircraft: AircraftInfo) -> bool:
    if match_type == "flagged_military_or_pia_or_ladd":
        return aircraft.flagged_military or aircraft.flagged_pia or aircraft.flagged_ladd

    patterns = _patterns(pattern)
    if not patterns:
        return False

    if match_type == "typecode":
        value = (aircraft.typecode or "").upper()
        return bool(value) and value in patterns

    if match_type == "registration":
        value = (aircraft.registration or "").upper()
        return bool(value) and value in patterns

    if match_type == "icao24":
        value = (aircraft.icao24 or "").upper()
        return bool(value) and value in patterns

    if match_type == "callsign_prefix":
        value = (aircraft.callsign or "").upper()
        return bool(value) and any(value.startswith(p) for p in patterns)

    if match_type == "operator_contains":
        value = (aircraft.operator or "").upper()
        return bool(value) and any(p in value for p in patterns)

    return False
