from app.matcher import AircraftInfo, matches


def test_typecode_match_is_exact_and_case_insensitive():
    aircraft = AircraftInfo(icao24="3c6444", typecode="a3st")
    assert matches("typecode", "A3ST", aircraft) is True
    assert matches("typecode", "A339", aircraft) is False


def test_typecode_match_supports_multiple_comma_separated_patterns():
    aircraft = AircraftInfo(icao24="3c6444", typecode="EUFI")
    assert matches("typecode", "EUFI,EFA", aircraft) is True


def test_registration_match_requires_exact_value():
    aircraft = AircraftInfo(icao24="3c6444", registration="F-GXLG")
    assert matches("registration", "F-GXLG, F-GXLH", aircraft) is True
    assert matches("registration", "F-GXLH", aircraft) is False


def test_icao24_match_is_case_insensitive():
    aircraft = AircraftInfo(icao24="3c6444")
    assert matches("icao24", "3C6444", aircraft) is True


def test_callsign_prefix_matches_start_of_callsign_only():
    aircraft = AircraftInfo(icao24="3c6444", callsign="RCH123")
    assert matches("callsign_prefix", "RCH,REACH", aircraft) is True
    assert matches("callsign_prefix", "DLH", aircraft) is False


def test_operator_contains_matches_substring():
    aircraft = AircraftInfo(icao24="3c6444", operator="Antonov Airlines")
    assert matches("operator_contains", "Antonov", aircraft) is True
    assert matches("operator_contains", "Lufthansa", aircraft) is False


def test_missing_field_never_matches():
    aircraft = AircraftInfo(icao24="3c6444")
    assert matches("typecode", "A3ST", aircraft) is False
    assert matches("callsign_prefix", "RCH", aircraft) is False
    assert matches("operator_contains", "Antonov", aircraft) is False


def test_empty_pattern_never_matches():
    aircraft = AircraftInfo(icao24="3c6444", typecode="A3ST")
    assert matches("typecode", "", aircraft) is False
    assert matches("typecode", " , ", aircraft) is False


def test_unknown_match_type_never_matches():
    aircraft = AircraftInfo(icao24="3c6444", typecode="A3ST")
    assert matches("bogus_type", "A3ST", aircraft) is False
