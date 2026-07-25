import requests

from app import planespotters


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
    "photos": [
        {
            "id": "1948036",
            "thumbnail": {"src": "https://t.plnspttrs.net/48683/1948036_b79e755c4a_t.jpg"},
            "thumbnail_large": {"src": "https://t.plnspttrs.net/48683/1948036_b79e755c4a_280.jpg"},
            "link": "https://www.planespotters.net/photo/1948036/d-aibd-lufthansa-airbus-a319-112",
            "photographer": "Cornelius Grossmann",
        }
    ]
}


def test_fetch_photo_parses_the_first_photo(monkeypatch):
    monkeypatch.setattr(planespotters.requests, "get", lambda *a, **k: FakeResponse(SAMPLE_RESPONSE))

    photo = planespotters.fetch_photo("3c6444")

    assert photo == {
        "thumbnail_url": "https://t.plnspttrs.net/48683/1948036_b79e755c4a_t.jpg",
        "large_url": "https://t.plnspttrs.net/48683/1948036_b79e755c4a_280.jpg",
        "link": "https://www.planespotters.net/photo/1948036/d-aibd-lufthansa-airbus-a319-112",
    }


def test_fetch_photo_sends_a_descriptive_user_agent(monkeypatch):
    # planespotters.net rejects generic library User-Agents with a 403 -
    # confirmed against the live API - so this header is not optional.
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse(SAMPLE_RESPONSE)

    monkeypatch.setattr(planespotters.requests, "get", fake_get)

    planespotters.fetch_photo("3c6444")

    assert "RareBirdAlert" in captured["headers"]["User-Agent"]
    assert "python-requests" not in captured["headers"]["User-Agent"]


def test_fetch_photo_returns_none_for_empty_icao24(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("should not call the API for an empty icao24")

    monkeypatch.setattr(planespotters.requests, "get", fail)

    assert planespotters.fetch_photo("") is None
    assert planespotters.fetch_photo(None) is None


def test_fetch_photo_returns_none_when_no_photos_found(monkeypatch):
    monkeypatch.setattr(planespotters.requests, "get", lambda *a, **k: FakeResponse({"photos": []}))

    assert planespotters.fetch_photo("000000") is None


def test_fetch_photo_returns_none_on_request_exception(monkeypatch):
    def raise_exc(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(planespotters.requests, "get", raise_exc)

    assert planespotters.fetch_photo("3c6444") is None


def test_fetch_photo_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(planespotters.requests, "get", lambda *a, **k: FakeResponse(status_code=403))

    assert planespotters.fetch_photo("3c6444") is None


def test_fetch_photo_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setattr(planespotters.requests, "get", lambda *a, **k: FakeResponse({"photos": "not-a-list"}))

    assert planespotters.fetch_photo("3c6444") is None
