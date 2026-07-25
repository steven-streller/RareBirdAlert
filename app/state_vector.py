"""Shared aircraft-state representation, used by every data source client
(app/opensky.py, app/adsblol.py, ...) and by app/flight_sources.py, which
merges results across sources. Deliberately dependency-free to avoid a
circular import between flight_sources.py and the individual source clients.
"""

from dataclasses import dataclass


@dataclass
class StateVector:
    icao24: str
    callsign: str | None = None
    on_ground: bool = False
    lat: float | None = None
    lon: float | None = None
    # Live type/registration - not every source provides these (OpenSky
    # doesn't; adsb.lol does). When absent, app/scheduler.py falls back to
    # the aircraft_db metadata cache.
    typecode: str | None = None
    registration: str | None = None
    # Source-reported flags (currently only adsb.lol's dbFlags bitmask sets
    # these) - a more reliable "special aircraft" signal than the
    # callsign-prefix heuristic, since it comes from the aircraft's actual
    # database record rather than a guess.
    flagged_military: bool = False
    flagged_pia: bool = False
    flagged_ladd: bool = False
