from tests.conftest import register


def test_builtin_categories_are_enabled_by_default(client):
    register(client, "alice@example.com")
    page = client.get("/watchlist")
    assert page.text.count("Aktiv") >= 4  # all seeded categories, and "Aktiv" != "Inaktiv"


def test_toggle_category_disables_and_reenables(client):
    register(client, "alice@example.com")
    client.post("/watchlist/category/military/toggle")
    page = client.get("/watchlist")
    assert "Inaktiv" in page.text

    client.post("/watchlist/category/military/toggle")
    page = client.get("/watchlist")
    assert page.text.count("Aktiv") >= 4


def test_toggle_unknown_category_is_a_noop(client):
    register(client, "alice@example.com")
    resp = client.post("/watchlist/category/not-a-real-category/toggle", follow_redirects=False)
    assert resp.headers["location"] == "/watchlist"


def test_builtin_categories_do_not_show_criterion_or_pattern(client):
    # Criterion and pattern are documented in docs/watchlist.md; showing them
    # again here was redundant, so the app only shows label + description.
    register(client, "alice@example.com")
    page = client.get("/watchlist")
    assert "Callsign-Präfix: GAF" not in page.text
    assert "adsb.lol-Flag (militärisch/PIA/LADD)" not in page.text


def test_add_watchlist_entry(client):
    register(client, "alice@example.com")
    resp = client.post(
        "/watchlist",
        data={"label": "Beluga XL", "match_type": "registration", "pattern": "F-GXLG, F-GXLH"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/watchlist?saved=1"

    page = client.get("/watchlist")
    assert "Beluga XL" in page.text
    assert "F-GXLG, F-GXLH" in page.text


def test_add_watchlist_entry_rejects_invalid_match_type(client):
    register(client, "alice@example.com")
    client.post("/watchlist", data={"label": "Bad Entry", "match_type": "not-a-type", "pattern": "X"})
    page = client.get("/watchlist")
    assert "Bad Entry" not in page.text


def test_add_watchlist_entry_requires_label_and_pattern(client):
    register(client, "alice@example.com")
    client.post("/watchlist", data={"label": "", "match_type": "typecode", "pattern": ""})
    page = client.get("/watchlist")
    assert "Noch keine eigenen Einträge." in page.text


def test_new_watchlist_entry_is_enabled_by_default(client):
    register(client, "alice@example.com")
    client.post("/watchlist", data={"label": "Beluga XL", "match_type": "registration", "pattern": "F-GXLG"})
    page = client.get("/watchlist")
    assert "○ Inaktiv" not in page.text


def test_toggle_watchlist_entry_disables_and_reenables_own_entry_only(client):
    from fastapi.testclient import TestClient

    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    alice.post("/watchlist", data={"label": "Alice Entry", "match_type": "typecode", "pattern": "DC3"})

    # Bob can't toggle Alice's entry (id 1) even though he can guess the URL.
    bob.post("/watchlist/1/toggle")
    assert "○ Inaktiv" not in alice.get("/watchlist").text

    alice.post("/watchlist/1/toggle")
    assert "○ Inaktiv" in alice.get("/watchlist").text

    alice.post("/watchlist/1/toggle")
    assert "○ Inaktiv" not in alice.get("/watchlist").text


def test_delete_watchlist_entry_only_removes_own(client):
    from fastapi.testclient import TestClient

    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    alice.post("/watchlist", data={"label": "Alice Entry", "match_type": "typecode", "pattern": "DC3"})

    # Bob can't delete Alice's entry (id 1) even though he can guess the URL.
    bob.post("/watchlist/1/delete")
    assert "Alice Entry" in alice.get("/watchlist").text

    alice.post("/watchlist/1/delete")
    assert "Alice Entry" not in alice.get("/watchlist").text
