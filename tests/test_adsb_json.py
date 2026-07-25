from app import adsb_json


def test_parse_aircraft_list_maps_fields():
    data = {
        "ac": [
            {
                "hex": "3c6444",
                "flight": "GAF123  ",
                "alt_baro": 500,
                "lat": 50.1,
                "lon": 8.5,
                "t": "eufi",
                "r": "31+00",
                "dbFlags": 0,
            },
            {
                "hex": "4b1a12",
                "flight": None,
                "alt_baro": "ground",
                "lat": 50.2,
                "lon": 8.6,
                "t": None,
                "r": None,
                "dbFlags": 0,
            },
        ]
    }

    states = adsb_json.parse_aircraft_list(data)

    assert len(states) == 2
    assert states[0].icao24 == "3c6444"
    assert states[0].callsign == "GAF123"
    assert states[0].on_ground is False
    assert states[0].typecode == "EUFI"
    assert states[0].registration == "31+00"
    assert states[1].on_ground is True
    assert states[1].callsign is None


def test_parse_aircraft_list_skips_items_without_hex():
    data = {"ac": [{"hex": "", "flight": "DLH1", "alt_baro": 500}]}
    assert adsb_json.parse_aircraft_list(data) == []


def test_parse_aircraft_list_handles_missing_ac_key():
    assert adsb_json.parse_aircraft_list({}) == []


def test_parse_aircraft_list_sets_flagged_fields_from_db_flags():
    data = {
        "ac": [
            {"hex": "aaaaaa", "flight": None, "alt_baro": 500, "dbFlags": adsb_json.DBFLAG_MILITARY},
            {
                "hex": "bbbbbb",
                "flight": None,
                "alt_baro": 500,
                "dbFlags": adsb_json.DBFLAG_PIA | adsb_json.DBFLAG_LADD,
            },
            {"hex": "cccccc", "flight": None, "alt_baro": 500, "dbFlags": 0},
            {"hex": "dddddd", "flight": None, "alt_baro": 500},  # dbFlags absent entirely
        ]
    }

    states = {s.icao24: s for s in adsb_json.parse_aircraft_list(data)}

    assert states["aaaaaa"].flagged_military is True
    assert states["aaaaaa"].flagged_pia is False
    assert states["bbbbbb"].flagged_pia is True
    assert states["bbbbbb"].flagged_ladd is True
    assert states["bbbbbb"].flagged_military is False
    assert states["cccccc"].flagged_military is False
    assert states["dddddd"].flagged_military is False
