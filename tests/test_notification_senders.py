"""Unit tests for the actual send_* HTTP/SMTP logic in app/notifications.py.

tests/test_notifications.py covers the CHANNELS registry and the
enabled_channels/notify_all dispatch layer above these - this file is about
the individual channels' request-building, success and failure handling,
which was previously only exercised through the "not configured" early
returns.
"""

import requests

from app import notifications


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _raise_connection_error(*_args, **_kwargs):
    raise requests.ConnectionError("boom")


# --- Pushover ------------------------------------------------------------------


def test_send_pushover_success_includes_url(monkeypatch):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse(200)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    ok = notifications.send_pushover(
        {"pushover_user_key": "u", "pushover_api_token": "t"}, "Titel", "Nachricht", url="https://example.com"
    )

    assert ok is True
    assert captured["data"]["user"] == "u"
    assert captured["data"]["token"] == "t"
    assert captured["data"]["url"] == "https://example.com"


def test_send_pushover_missing_config_returns_false():
    assert notifications.send_pushover({"pushover_user_key": "", "pushover_api_token": ""}, "t", "m") is False


def test_send_pushover_request_failure_returns_false(monkeypatch):
    monkeypatch.setattr(notifications.requests, "post", _raise_connection_error)
    ok = notifications.send_pushover({"pushover_user_key": "u", "pushover_api_token": "t"}, "t", "m")
    assert ok is False


# --- ntfy ------------------------------------------------------------------------


def test_send_ntfy_success_with_token_and_url(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse(200)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    ok = notifications.send_ntfy(
        {"ntfy_server_url": "https://ntfy.sh/", "ntfy_topic": "topic1", "ntfy_token": "abc"},
        "Titel",
        "Nachricht",
        url="https://example.com",
    )

    assert ok is True
    assert captured["url"] == "https://ntfy.sh/topic1"
    assert captured["headers"]["Authorization"] == "Bearer abc"
    assert captured["headers"]["Click"] == b"https://example.com"


def test_send_ntfy_missing_config_returns_false():
    assert notifications.send_ntfy({"ntfy_server_url": "", "ntfy_topic": ""}, "t", "m") is False


def test_send_ntfy_request_failure_returns_false(monkeypatch):
    monkeypatch.setattr(notifications.requests, "post", _raise_connection_error)
    ok = notifications.send_ntfy({"ntfy_server_url": "https://ntfy.sh", "ntfy_topic": "t"}, "t", "m")
    assert ok is False


# --- Telegram --------------------------------------------------------------------


def test_send_telegram_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse(200)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    ok = notifications.send_telegram({"telegram_bot_token": "tok", "telegram_chat_id": "123"}, "t", "m")

    assert ok is True
    assert captured["url"] == "https://api.telegram.org/bottok/sendMessage"
    assert captured["json"]["chat_id"] == "123"


def test_send_telegram_missing_config_returns_false():
    assert notifications.send_telegram({"telegram_bot_token": "", "telegram_chat_id": ""}, "t", "m") is False


def test_send_telegram_request_failure_returns_false(monkeypatch):
    monkeypatch.setattr(notifications.requests, "post", _raise_connection_error)
    ok = notifications.send_telegram({"telegram_bot_token": "tok", "telegram_chat_id": "123"}, "t", "m")
    assert ok is False


# --- Discord ---------------------------------------------------------------------


def test_send_discord_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse(200)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    ok = notifications.send_discord({"discord_webhook_url": "https://discord.example/hook"}, "t", "m")

    assert ok is True
    assert captured["url"] == "https://discord.example/hook"
    assert "t" in captured["json"]["content"]


def test_send_discord_request_failure_returns_false(monkeypatch):
    monkeypatch.setattr(notifications.requests, "post", _raise_connection_error)
    ok = notifications.send_discord({"discord_webhook_url": "https://discord.example/hook"}, "t", "m")
    assert ok is False


# --- Generic webhook ---------------------------------------------------------------


def test_send_webhook_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse(200)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    ok = notifications.send_webhook({"webhook_url": "https://example.com/hook"}, "t", "m", url="https://x")

    assert ok is True
    assert captured["json"] == {"title": "t", "message": "m", "url": "https://x"}


def test_send_webhook_missing_config_returns_false():
    assert notifications.send_webhook({"webhook_url": ""}, "t", "m") is False


def test_send_webhook_request_failure_returns_false(monkeypatch):
    monkeypatch.setattr(notifications.requests, "post", _raise_connection_error)
    ok = notifications.send_webhook({"webhook_url": "https://example.com/hook"}, "t", "m")
    assert ok is False


# --- Email -------------------------------------------------------------------------


def _email_cfg(**overrides):
    cfg = {
        "email_smtp_host": "smtp.example.com",
        "email_to": "dest@example.com",
        "email_smtp_port": "587",
        "email_use_tls": "true",
        "email_smtp_user": "",
        "email_smtp_password": "",
        "email_from": "",
    }
    cfg.update(overrides)
    return cfg


class FakeSMTP:
    def __init__(self, host, port, timeout=None, log=None):
        self.log = log if log is not None else {}
        self.log["host"] = host
        self.log["port"] = port
        self.log["tls"] = False

    def starttls(self):
        self.log["tls"] = True

    def login(self, user, password):
        self.log["login"] = (user, password)

    def sendmail(self, from_addr, to_addrs, msg):
        self.log["sent"] = (from_addr, to_addrs)

    def quit(self):
        self.log["quit"] = True


def test_send_email_starttls_success_with_login(monkeypatch):
    log = {}
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda host, port, timeout=None: FakeSMTP(host, port, log=log))

    ok = notifications.send_email(
        _email_cfg(email_smtp_user="user@example.com", email_smtp_password="secret", email_from="user@example.com"),
        "Titel",
        "Nachricht",
    )

    assert ok is True
    assert log["tls"] is True
    assert log["login"] == ("user@example.com", "secret")
    assert log["sent"][1] == ["dest@example.com"]
    assert log["quit"] is True


def test_send_email_skips_login_when_no_smtp_user(monkeypatch):
    log = {}
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda host, port, timeout=None: FakeSMTP(host, port, log=log))

    ok = notifications.send_email(_email_cfg(), "t", "m")

    assert ok is True
    assert "login" not in log


def test_send_email_ssl_port_uses_smtp_ssl(monkeypatch):
    log = {}
    monkeypatch.setattr(
        notifications.smtplib, "SMTP_SSL", lambda host, port, timeout=None: FakeSMTP(host, port, log=log)
    )

    ok = notifications.send_email(_email_cfg(email_smtp_port="465"), "t", "m")

    assert ok is True
    assert log["port"] == 465
    assert "tls" not in log or log.get("tls") is False  # starttls() never called on the SSL path


def test_send_email_missing_config_returns_false():
    assert notifications.send_email({"email_smtp_host": "", "email_to": ""}, "t", "m") is False


def test_send_email_smtp_exception_returns_false(monkeypatch):
    import smtplib

    def raise_exc(host, port, timeout=None):
        raise smtplib.SMTPConnectError(421, "cannot connect")

    monkeypatch.setattr(notifications.smtplib, "SMTP", raise_exc)

    ok = notifications.send_email(_email_cfg(), "t", "m")

    assert ok is False


def test_send_email_os_error_returns_false(monkeypatch):
    def raise_exc(host, port, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(notifications.smtplib, "SMTP", raise_exc)

    assert notifications.send_email(_email_cfg(), "t", "m") is False
