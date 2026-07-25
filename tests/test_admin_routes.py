from fastapi.testclient import TestClient

from tests.conftest import register


def test_first_registered_user_is_admin(client):
    register(client, "alice@example.com")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Admin" in page.text


def test_second_registered_user_is_not_admin(client):
    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    resp = bob.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"


def test_non_admin_cannot_post_to_admin(client, monkeypatch):
    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    resp = bob.post(
        "/admin", data={"_section": "general", "poll_interval_seconds": "999"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"

    # bob's attempt must not have changed the global setting alice sees
    admin_page = alice.get("/admin")
    assert 'value="999"' not in admin_page.text


def test_non_admin_cannot_trigger_poll_now(client, monkeypatch):
    from app.main import app

    calls = []
    monkeypatch.setattr("app.main.poll_job", lambda: calls.append(1))

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    resp = bob.post("/poll-now", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"
    assert calls == []


def test_admin_nav_link_only_shown_to_admin(client):
    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    assert 'href="/admin"' in alice.get("/settings").text
    assert 'href="/admin"' not in bob.get("/settings").text


def test_general_section_poll_interval_is_global(client):
    register(client, "alice@example.com")
    client.post("/admin", data={"_section": "general", "poll_interval_seconds": "120"})

    page = client.get("/admin")
    assert 'value="120"' in page.text


def test_general_section_rejects_interval_below_minimum(client):
    register(client, "alice@example.com")
    client.post("/admin", data={"_section": "general", "poll_interval_seconds": "5"})

    page = client.get("/admin")
    assert 'value="30"' in page.text  # clamped to the minimum


def test_source_credential_editable_without_env_var(client, monkeypatch):
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    register(client, "alice@example.com")

    client.post(
        "/admin",
        data={
            "_section": "source_opensky",
            "source_enabled": "on",
            "opensky_client_id": "my-client-id",
            "opensky_client_secret": "my-secret",
        },
    )

    page = client.get("/admin")
    # the editable branch renders a name= attribute (so the browser submits
    # it); the locked branch (tested below) never does
    assert 'name="opensky_client_id"' in page.text
    assert 'value="my-client-id"' in page.text


def test_source_credential_locked_by_env_cannot_be_overridden(client, monkeypatch):
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "from-env")
    register(client, "alice@example.com")

    resp = client.post(
        "/admin",
        data={"_section": "source_opensky", "opensky_client_id": "attempted-override"},
    )
    assert resp.status_code in (200, 303)

    page = client.get("/admin")
    # locked fields render without a name= attribute so they can't be
    # submitted at all, and the attempted override was never persisted
    assert 'name="opensky_client_id"' not in page.text
    assert 'value="from-env"' in page.text
    assert "attempted-override" not in page.text


def test_toggle_source_enabled(client):
    register(client, "alice@example.com")

    client.post("/admin", data={"_section": "source_adsblol"})  # source_enabled absent = unchecked
    page = client.get("/admin")
    assert 'id="source_adsblol"' in page.text
    assert page.text.split('id="source_adsblol"')[1].split("</details>")[0].count(">Aktiv<") == 0

    client.post("/admin", data={"_section": "source_adsblol", "source_enabled": "on"})
    page = client.get("/admin")
    assert page.text.split('id="source_adsblol"')[1].split("</details>")[0].count(">Aktiv<") == 1


def test_poll_now_triggers_poll_job(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.poll_job", lambda: calls.append(1))

    register(client, "alice@example.com")
    resp = client.post("/poll-now", follow_redirects=False)

    assert resp.headers["location"] == "/admin?polled=1"
    assert calls == [1]


def test_admin_page_shows_flash_after_manual_poll(client):
    register(client, "alice@example.com")
    page = client.get("/admin?polled=1")
    assert "Poll-Zyklus manuell angestoßen." in page.text


def test_save_admin_settings_falls_back_to_general_anchor_for_unknown_section(client):
    register(client, "alice@example.com")
    resp = client.post("/admin", data={"_section": "not-a-real-section"}, follow_redirects=False)
    assert resp.headers["location"] == "/admin?saved=general#general"
