import threading
import time

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300


class LoginRateLimiter:
    """In-memory sliding-window limiter for repeated attempts against a
    sensitive endpoint, keyed by (client IP, identifier) - no Redis,
    consistent with the single-container, single-process architecture.
    State resets on process restart, which is an acceptable trade-off here:
    a self-hosted instance's operator restarting their own container isn't
    the threat this defends against.

    Used for both /login (identifier is the attempted email, so different
    users behind the same IP - e.g. a shared office network - don't lock
    each other out) and /register (identifier is a fixed literal, since a
    registration attempt has no natural per-user identifier - each one uses
    a different, not-yet-existing email).
    """

    def __init__(self, max_attempts: int = MAX_ATTEMPTS, window_seconds: int = WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, key: tuple[str, str], now: float) -> list[float]:
        return [t for t in self._attempts.get(key, []) if now - t < self.window_seconds]

    def is_blocked(self, ip: str, email: str) -> bool:
        key = (ip, email)
        now = time.time()
        with self._lock:
            attempts = self._prune_locked(key, now)
            self._attempts[key] = attempts
            return len(attempts) >= self.max_attempts

    def record_failure(self, ip: str, email: str) -> None:
        key = (ip, email)
        now = time.time()
        with self._lock:
            attempts = self._prune_locked(key, now)
            attempts.append(now)
            self._attempts[key] = attempts

    def reset(self, ip: str, email: str) -> None:
        with self._lock:
            self._attempts.pop((ip, email), None)


login_rate_limiter = LoginRateLimiter()
register_rate_limiter = LoginRateLimiter()
