from fastapi.testclient import TestClient

from tests.conftest import register


def test_channel_settings_are_isolated_per_user(client):
    from app.main import app

    alice = client
    register(alice, "alice@example.com")
    bob = TestClient(app)
    register(bob, "bob@example.com")

    alice.post(
        "/settings",
        data={
            "_section": "ntfy",
            "ntfy_enabled": "on",
            "ntfy_server_url": "https://ntfy.sh",
            "ntfy_topic": "alice-topic",
        },
    )

    alice_settings = alice.get("/settings")
    assert 'name="ntfy_enabled" checked' in alice_settings.text
    assert 'value="alice-topic"' in alice_settings.text

    bob_settings = bob.get("/settings")
    assert 'name="ntfy_enabled" checked' not in bob_settings.text
    assert "alice-topic" not in bob_settings.text


def test_settings_page_does_not_expose_admin_settings(client):
    register(client, "alice@example.com")
    page = client.get("/settings")
    assert "Poll-Intervall" not in page.text
    assert "Datenquellen" not in page.text


def test_test_notification_endpoint_reports_failure_when_unconfigured(client):
    register(client, "alice@example.com")
    resp = client.post("/settings/test/pushover", follow_redirects=False)
    assert resp.headers["location"] == "/settings?tested=fail#pushover"


def test_test_notification_rejects_unknown_channel(client):
    register(client, "alice@example.com")
    resp = client.post("/settings/test/not-a-real-channel", follow_redirects=False)
    assert resp.headers["location"] == "/settings?tested=fail"
