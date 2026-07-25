from app import rate_limit
from app.rate_limit import LoginRateLimiter


def test_is_blocked_is_false_before_any_failures():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300)
    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is False


def test_is_blocked_becomes_true_after_max_attempts():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300)
    for _ in range(3):
        limiter.record_failure("1.2.3.4", "alice@example.com")

    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is True


def test_is_blocked_stays_false_below_max_attempts():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=300)
    for _ in range(2):
        limiter.record_failure("1.2.3.4", "alice@example.com")

    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is False


def test_reset_clears_recorded_failures():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=300)
    limiter.record_failure("1.2.3.4", "alice@example.com")
    limiter.record_failure("1.2.3.4", "alice@example.com")
    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is True

    limiter.reset("1.2.3.4", "alice@example.com")

    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is False


def test_different_ip_or_email_are_tracked_independently():
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=300)
    limiter.record_failure("1.2.3.4", "alice@example.com")

    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is True
    assert limiter.is_blocked("5.6.7.8", "alice@example.com") is False
    assert limiter.is_blocked("1.2.3.4", "bob@example.com") is False


def test_attempts_outside_the_window_no_longer_count(monkeypatch):
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
    fake_now = [1000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: fake_now[0])

    limiter.record_failure("1.2.3.4", "alice@example.com")
    limiter.record_failure("1.2.3.4", "alice@example.com")
    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is True

    fake_now[0] += 61  # past the window
    assert limiter.is_blocked("1.2.3.4", "alice@example.com") is False
