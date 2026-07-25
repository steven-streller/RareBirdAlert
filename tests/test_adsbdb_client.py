import requests

from app import adsbdb


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data if json_data is not None else {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


SAMPLE_RESPONSE = {
    "response": {
        "flightroute": {
            "callsign": "BAW123",
            "origin": {"icao_code": "EGLL", "name": "London Heathrow Airport"},
            "destination": {"icao_code": "OTHH", "name": "Hamad International Airport"},
        }
    }
}


def test_fetch_route_parses_origin_and_destination(monkeypatch):
    monkeypatch.setattr(adsbdb.requests, "get", lambda *a, **k: FakeResponse(SAMPLE_RESPONSE))

    route = adsbdb.fetch_route("BAW123")

    assert route == {
        "origin_icao": "EGLL",
        "origin_name": "London Heathrow Airport",
        "destination_icao": "OTHH",
        "destination_name": "Hamad International Airport",
    }


def test_fetch_route_returns_none_for_empty_callsign(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("should not call the API for an empty callsign")

    monkeypatch.setattr(adsbdb.requests, "get", fail)

    assert adsbdb.fetch_route("") is None
    assert adsbdb.fetch_route(None) is None


def test_fetch_route_returns_none_on_unknown_callsign_404(monkeypatch):
    monkeypatch.setattr(
        adsbdb.requests, "get", lambda *a, **k: FakeResponse({"response": "unknown callsign"}, status_code=404)
    )

    assert adsbdb.fetch_route("ZZZZZZ99") is None


def test_fetch_route_returns_none_on_request_exception(monkeypatch):
    def raise_exc(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(adsbdb.requests, "get", raise_exc)

    assert adsbdb.fetch_route("BAW123") is None


def test_fetch_route_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(adsbdb.requests, "get", lambda *a, **k: FakeResponse(status_code=500))

    assert adsbdb.fetch_route("BAW123") is None


def test_fetch_route_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setattr(adsbdb.requests, "get", lambda *a, **k: FakeResponse({"response": "unexpected string"}))

    assert adsbdb.fetch_route("BAW123") is None


def test_fetch_route_returns_none_when_flightroute_key_missing(monkeypatch):
    monkeypatch.setattr(adsbdb.requests, "get", lambda *a, **k: FakeResponse({"response": {"no_flightroute_here": 1}}))

    assert adsbdb.fetch_route("BAW123") is None


def test_fetch_route_strips_whitespace_and_encodes_url(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResponse(SAMPLE_RESPONSE)

    monkeypatch.setattr(adsbdb.requests, "get", fake_get)

    adsbdb.fetch_route("  BAW 123  ")

    assert captured["url"] == f"{adsbdb.CALLSIGN_URL}/BAW%20123"
