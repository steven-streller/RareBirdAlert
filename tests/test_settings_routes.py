from fastapi.testclient import TestClient

from app.main import CHANNELS, _safe_channel_anchor
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


def test_settings_page_shows_flash_for_successful_test(client):
    register(client, "alice@example.com")
    page = client.get("/settings?tested=ok")
    assert "Test-Benachrichtigung gesendet." in page.text


def test_settings_page_shows_flash_for_failed_test(client):
    register(client, "alice@example.com")
    page = client.get("/settings?tested=fail")
    assert "Test-Benachrichtigung fehlgeschlagen" in page.text


def test_save_settings_persists_channel_checkbox_field(client):
    register(client, "alice@example.com")
    client.post(
        "/settings",
        data={
            "_section": "email",
            "email_enabled": "on",
            "email_smtp_host": "smtp.example.com",
            "email_to": "dest@example.com",
            "email_use_tls": "on",
        },
    )

    page = client.get("/settings")
    assert 'name="email_use_tls" checked' in page.text


def test_safe_channel_anchor_never_echoes_unknown_input():
    # Regression test for a CodeQL "URL redirection from remote source"
    # (CWE-601) finding: an earlier version returned `value` itself on a
    # match instead of a literal from the fixed CHANNELS collection, which
    # static analysis treats as attacker-controlled data reaching a redirect.
    for known in CHANNELS:
        assert _safe_channel_anchor(known) == known
    assert _safe_channel_anchor("javascript:alert(1)") == "general"
    assert _safe_channel_anchor("//evil.example.com") == "general"
    assert _safe_channel_anchor("") == "general"


def test_save_settings_redirect_never_reflects_an_unknown_section(client):
    register(client, "alice@example.com")
    resp = client.post(
        "/settings", data={"_section": "//evil.example.com"}, follow_redirects=False
    )
    assert resp.headers["location"] == "/settings?saved=general#general"
