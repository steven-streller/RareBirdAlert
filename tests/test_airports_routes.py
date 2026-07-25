from fastapi.testclient import TestClient

from tests.conftest import register


def test_airport_search_finds_by_icao(client):
    register(client, "alice@example.com")
    resp = client.get("/airports/search?q=EDDF")
    assert resp.status_code == 200
    assert "Frankfurt" in resp.text


def test_airport_search_empty_query_returns_no_results(client):
    register(client, "alice@example.com")
    resp = client.get("/airports/search?q=")
    assert "item-row" not in resp.text


def test_add_airport_creates_watch(client):
    register(client, "alice@example.com")
    resp = client.post("/airports", data={"icao": "eddf", "radius_km": "20"}, follow_redirects=False)
    assert resp.headers["location"] == "/airports?added=1"

    page = client.get("/airports")
    assert "EDDF" in page.text
    assert "20" in page.text


def test_add_airport_unknown_icao_redirects_with_error(client):
    register(client, "alice@example.com")
    resp = client.post("/airports", data={"icao": "ZZZZ", "radius_km": "15"}, follow_redirects=False)
    assert resp.headers["location"] == "/airports?error=notfound"


def test_add_airport_twice_updates_radius_instead_of_duplicating(client):
    register(client, "alice@example.com")
    client.post("/airports", data={"icao": "EDDF", "radius_km": "10"})
    client.post("/airports", data={"icao": "EDDF", "radius_km": "50"})

    page = client.get("/airports")
    assert page.text.count("EDDF") == 1
    assert "50" in page.text


def test_delete_airport_only_removes_own_watch(client):
    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    alice.post("/airports", data={"icao": "EDDF", "radius_km": "15"})
    bob.post("/airports", data={"icao": "EDDF", "radius_km": "15"})

    # Bob's watch has id 2 (Alice's is id 1) - Bob must not be able to delete Alice's.
    bob.post("/airports/1/delete")
    assert "EDDF" in alice.get("/airports").text

    alice.post("/airports/1/delete")
    assert "EDDF" not in alice.get("/airports").text


def test_add_airport_falls_back_to_default_radius_on_invalid_value(client):
    register(client, "alice@example.com")
    client.post("/airports", data={"icao": "EDDF", "radius_km": "not-a-number"})

    page = client.get("/airports")
    assert "15" in page.text  # default radius, since the given value didn't parse
