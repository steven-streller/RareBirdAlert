import logging

from app.main import _HealthCheckLogFilter
from app.rate_limit import LoginRateLimiter
from tests.conftest import register


def _log_record(message: str) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)


def test_healthz_does_not_require_login(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_does_not_require_login(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_returns_503_when_the_database_is_unreachable(client, monkeypatch):
    from app.main import engine

    def broken_connect(*args, **kwargs):
        raise OSError("simulated database failure")

    monkeypatch.setattr(engine, "connect", broken_connect)

    resp = client.get("/readyz")
    assert resp.status_code == 503


def test_health_check_log_filter_suppresses_probe_and_metrics_hits():
    filt = _HealthCheckLogFilter()
    for path in ("/healthz", "/readyz", "/metrics"):
        assert filt.filter(_log_record(f'"GET {path} HTTP/1.1" 200')) is False


def test_health_check_log_filter_allows_other_requests():
    filt = _HealthCheckLogFilter()
    assert filt.filter(_log_record('"GET /settings HTTP/1.1" 200')) is True


def test_unauthenticated_dashboard_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_register_creates_account_and_logs_in(client):
    resp = register(client, "alice@example.com")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200


def test_register_rejects_invalid_email(client):
    resp = client.post(
        "/register",
        data={"email": "not-an-email", "password": "testpassword1", "password_confirm": "testpassword1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/register?error=email"


def test_register_rejects_short_password(client):
    resp = client.post(
        "/register",
        data={"email": "bob@example.com", "password": "short", "password_confirm": "short"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/register?error=password_length"


def test_register_rejects_password_mismatch(client):
    resp = client.post(
        "/register",
        data={"email": "carl@example.com", "password": "testpassword1", "password_confirm": "different1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/register?error=password_mismatch"


def test_register_rejects_duplicate_email(client):
    register(client, "dupe@example.com")
    resp = register(client, "dupe@example.com")
    assert resp.headers["location"] == "/register?error=taken"


def test_registration_disabled_blocks_new_signups(client, monkeypatch):
    monkeypatch.setattr("app.main.REGISTRATION_ENABLED", False)
    resp = register(client, "toolate@example.com")
    assert resp.headers["location"] == "/register"

    page = client.get("/register")
    assert "deaktiviert" in page.text


def test_login_wrong_password_fails(client):
    register(client, "dave@example.com", password="correctpass1")
    client.post("/logout")

    resp = client.post(
        "/login", data={"email": "dave@example.com", "password": "wrongpass1"}, follow_redirects=False
    )
    assert resp.headers["location"] == "/login?error=1"


def test_login_success_allows_dashboard_access(client):
    register(client, "erin@example.com", password="correctpass1")
    client.post("/logout")

    resp = client.post(
        "/login", data={"email": "erin@example.com", "password": "correctpass1"}, follow_redirects=False
    )
    assert resp.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_logout_clears_session(client):
    register(client, "frank@example.com")
    client.post("/logout")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_blocked_after_max_failed_attempts(client, monkeypatch):
    monkeypatch.setattr("app.main.login_rate_limiter", LoginRateLimiter(max_attempts=3, window_seconds=300))
    register(client, "ratelimited@example.com", password="correctpass1")
    client.post("/logout")

    for _ in range(3):
        client.post("/login", data={"email": "ratelimited@example.com", "password": "wrongpass1"})

    resp = client.post(
        "/login",
        data={"email": "ratelimited@example.com", "password": "correctpass1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/login?error=ratelimited"


def test_login_page_shows_ratelimited_message(client):
    page = client.get("/login?error=ratelimited")
    assert "Zu viele Fehlversuche" in page.text


def test_successful_login_resets_the_rate_limit_counter(client, monkeypatch):
    monkeypatch.setattr("app.main.login_rate_limiter", LoginRateLimiter(max_attempts=3, window_seconds=300))
    register(client, "resets@example.com", password="correctpass1")
    client.post("/logout")

    client.post("/login", data={"email": "resets@example.com", "password": "wrongpass1"})
    client.post("/login", data={"email": "resets@example.com", "password": "correctpass1"})
    client.post("/logout")

    client.post("/login", data={"email": "resets@example.com", "password": "wrongpass1"})
    resp = client.post(
        "/login",
        data={"email": "resets@example.com", "password": "correctpass1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/"


def test_register_blocked_after_max_attempts_from_the_same_ip(client, monkeypatch):
    monkeypatch.setattr("app.main.register_rate_limiter", LoginRateLimiter(max_attempts=3, window_seconds=300))

    for i in range(3):
        client.post(
            "/register",
            data={
                "email": f"spammer{i}@example.com",
                "password": "testpassword1",
                "password_confirm": "testpassword1",
            },
        )

    resp = client.post(
        "/register",
        data={"email": "onemore@example.com", "password": "testpassword1", "password_confirm": "testpassword1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/register?error=ratelimited"


def test_register_page_shows_ratelimited_message(client):
    page = client.get("/register?error=ratelimited")
    assert "Zu viele Registrierungsversuche" in page.text


def test_register_rate_limit_counts_failed_attempts_too(client, monkeypatch):
    # Unlike /login, every attempt counts here - including ones that fail
    # validation (e.g. password too short) - the abuse this guards against
    # is hammering the endpoint at all, not just successful signups.
    monkeypatch.setattr("app.main.register_rate_limiter", LoginRateLimiter(max_attempts=2, window_seconds=300))

    client.post("/register", data={"email": "bad1@example.com", "password": "short", "password_confirm": "short"})
    client.post("/register", data={"email": "bad2@example.com", "password": "short", "password_confirm": "short"})

    resp = client.post(
        "/register",
        data={"email": "good@example.com", "password": "testpassword1", "password_confirm": "testpassword1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/register?error=ratelimited"


def test_register_page_redirects_when_already_logged_in(client):
    register(client, "gina@example.com")
    resp = client.get("/register", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_page_redirects_when_already_logged_in(client):
    register(client, "hank@example.com")
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
