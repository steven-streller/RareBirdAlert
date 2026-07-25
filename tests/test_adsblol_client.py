import requests

from app import adsblol


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_states_parses_ac_items(monkeypatch):
    sample = {
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
    monkeypatch.setattr(adsblol._session, "get", lambda *a, **k: FakeResponse(sample))

    states = adsblol.fetch_states({}, 50.0, 8.0, 15.0)

    assert len(states) == 2
    assert states[0].icao24 == "3c6444"
    assert states[0].callsign == "GAF123"
    assert states[0].on_ground is False
    assert states[0].typecode == "EUFI"
    assert states[0].registration == "31+00"
    assert states[1].on_ground is True
    assert states[1].callsign is None


def test_fetch_states_skips_items_without_hex(monkeypatch):
    sample = {"ac": [{"hex": "", "flight": "DLH1", "alt_baro": 500}]}
    monkeypatch.setattr(adsblol._session, "get", lambda *a, **k: FakeResponse(sample))

    assert adsblol.fetch_states({}, 50.0, 8.0, 15.0) == []


def test_fetch_states_sets_flagged_fields_from_db_flags(monkeypatch):
    sample = {
        "ac": [
            {"hex": "aaaaaa", "flight": None, "alt_baro": 500, "dbFlags": adsblol.DBFLAG_MILITARY},
            {
                "hex": "bbbbbb",
                "flight": None,
                "alt_baro": 500,
                "dbFlags": adsblol.DBFLAG_PIA | adsblol.DBFLAG_LADD,
            },
            {"hex": "cccccc", "flight": None, "alt_baro": 500, "dbFlags": 0},
        ]
    }
    monkeypatch.setattr(adsblol._session, "get", lambda *a, **k: FakeResponse(sample))

    states = {s.icao24: s for s in adsblol.fetch_states({}, 50.0, 8.0, 15.0)}

    assert states["aaaaaa"].flagged_military is True
    assert states["aaaaaa"].flagged_pia is False
    assert states["bbbbbb"].flagged_pia is True
    assert states["bbbbbb"].flagged_ladd is True
    assert states["bbbbbb"].flagged_military is False
    assert states["cccccc"].flagged_military is False
    assert states["cccccc"].flagged_pia is False
    assert states["cccccc"].flagged_ladd is False


def test_fetch_states_converts_and_clamps_radius(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResponse({"ac": []})

    monkeypatch.setattr(adsblol._session, "get", fake_get)

    adsblol.fetch_states({}, 50.0, 8.0, 999999)  # far beyond the 250nm cap

    assert captured["url"] == f"{adsblol.BASE_URL}/50.0/8.0/250"


def test_fetch_states_returns_empty_list_on_request_exception(monkeypatch):
    def raise_exc(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(adsblol._session, "get", raise_exc)

    assert adsblol.fetch_states({}, 50.0, 8.0, 15.0) == []
