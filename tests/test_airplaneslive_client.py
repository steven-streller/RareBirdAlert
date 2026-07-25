import requests

from app import airplaneslive


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_states_parses_response_via_shared_parser(monkeypatch):
    # detailed field-mapping/dbFlags behavior is covered once in
    # test_adsb_json.py - this just confirms airplaneslive.py is wired up to it
    sample = {"ac": [{"hex": "ae1525", "flight": "MOAB08  ", "alt_baro": 25975, "t": "c30j", "r": "167109"}]}
    monkeypatch.setattr(airplaneslive._session, "get", lambda *a, **k: FakeResponse(sample))

    states = airplaneslive.fetch_states({}, 50.0, 8.0, 15.0)

    assert len(states) == 1
    assert states[0].icao24 == "ae1525"
    assert states[0].typecode == "C30J"


def test_fetch_states_converts_and_clamps_radius(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResponse({"ac": []})

    monkeypatch.setattr(airplaneslive._session, "get", fake_get)

    airplaneslive.fetch_states({}, 50.0, 8.0, 999999)  # far beyond the 250nm cap

    assert captured["url"] == f"{airplaneslive.BASE_URL}/50.0/8.0/250"


def test_fetch_states_returns_empty_list_on_request_exception(monkeypatch):
    def raise_exc(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(airplaneslive._session, "get", raise_exc)

    assert airplaneslive.fetch_states({}, 50.0, 8.0, 15.0) == []


def test_fetch_states_returns_empty_list_on_http_error(monkeypatch):
    monkeypatch.setattr(airplaneslive._session, "get", lambda *a, **k: FakeResponse(status_code=429))

    assert airplaneslive.fetch_states({}, 50.0, 8.0, 15.0) == []
