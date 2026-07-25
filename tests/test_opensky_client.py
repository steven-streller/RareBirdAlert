import requests

from app import opensky


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_bounding_box_is_centered_on_the_point():
    lamin, lomin, lamax, lomax = opensky._bounding_box(50.0, 8.0, 15.0)
    assert lamin < 50.0 < lamax
    assert lomin < 8.0 < lomax
    # roughly symmetric around the center point
    assert abs((lamax - 50.0) - (50.0 - lamin)) < 1e-6


def test_fetch_states_parses_state_vectors(monkeypatch):
    sample = {
        "states": [
            ["3c6444", "DLH123  ", "Germany", 0, 0, 8.5, 50.1, 500, False, 200, 90, 0, None, 500, None, False, 0],
            ["4b1a12", "SWR456  ", "Switzerland", 0, 0, 8.6, 50.2, 0, True, 0, 90, 0, None, 0, None, False, 0],
        ]
    }
    monkeypatch.setattr(opensky._session, "get", lambda *a, **k: FakeResponse(sample))
    monkeypatch.setattr(opensky, "_get_access_token", lambda: None)

    states = opensky.fetch_states(50.0, 8.0, 15.0)

    assert len(states) == 2
    assert states[0].icao24 == "3c6444"
    assert states[0].callsign == "DLH123"
    assert states[0].on_ground is False
    assert states[1].on_ground is True


def test_fetch_states_skips_rows_without_icao24(monkeypatch):
    sample = {"states": [["", "DLH123", "Germany", 0, 0, 8.5, 50.1, 500, False, 200, 90, 0, None, 500, None, False, 0]]}
    monkeypatch.setattr(opensky._session, "get", lambda *a, **k: FakeResponse(sample))
    monkeypatch.setattr(opensky, "_get_access_token", lambda: None)

    assert opensky.fetch_states(50.0, 8.0, 15.0) == []


def test_fetch_states_returns_empty_list_on_rate_limit(monkeypatch):
    monkeypatch.setattr(opensky._session, "get", lambda *a, **k: FakeResponse(status_code=429))
    monkeypatch.setattr(opensky, "_get_access_token", lambda: None)

    assert opensky.fetch_states(50.0, 8.0, 15.0) == []


def test_fetch_states_returns_empty_list_on_request_exception(monkeypatch):
    def raise_exc(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(opensky._session, "get", raise_exc)
    monkeypatch.setattr(opensky, "_get_access_token", lambda: None)

    assert opensky.fetch_states(50.0, 8.0, 15.0) == []


def test_fetch_states_sends_bearer_token_when_available(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse({"states": []})

    monkeypatch.setattr(opensky._session, "get", fake_get)
    monkeypatch.setattr(opensky, "_get_access_token", lambda: "fake-token")

    opensky.fetch_states(50.0, 8.0, 15.0)

    assert captured["headers"]["Authorization"] == "Bearer fake-token"


def test_get_access_token_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    assert opensky._get_access_token() is None
