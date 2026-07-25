"""Pure aircraft/category matching logic - no DB or network access, so it's
cheap to unit test in isolation from the scheduler and OpenSky client."""

from dataclasses import dataclass

MATCH_TYPES = ("typecode", "registration", "icao24", "callsign_prefix", "operator_contains")


@dataclass
class AircraftInfo:
    icao24: str
    callsign: str | None = None
    registration: str | None = None
    typecode: str | None = None
    operator: str | None = None


def _patterns(pattern: str) -> list[str]:
    return [p.strip().upper() for p in pattern.split(",") if p.strip()]


def matches(match_type: str, pattern: str, aircraft: AircraftInfo) -> bool:
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
