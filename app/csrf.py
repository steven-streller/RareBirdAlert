import secrets

from fastapi import HTTPException, Request

SESSION_CSRF_KEY = "csrf_token"


def get_or_create_csrf_token(request: Request) -> str:
    """Exposed to Jinja as `csrf_token(request)` - lazily creates one token
    per session and reuses it for every form on every page in that session.
    """
    return request.session.setdefault(SESSION_CSRF_KEY, secrets.token_urlsafe(32))


def verify_csrf(request: Request, submitted_token: str | None) -> None:
    """Defense-in-depth on top of the session cookie's `same_site="lax"`
    (app/main.py), which already blocks the classic cross-site form-post CSRF
    in modern browsers - this additionally covers older browsers and any
    future same_site change. Uses constant-time comparison since this is a
    secret-equality check, however low-value a timing leak here would be.
    """
    expected = request.session.get(SESSION_CSRF_KEY)
    if not expected or not submitted_token or not secrets.compare_digest(expected, submitted_token):
        raise HTTPException(status_code=403, detail="CSRF-Token ungültig oder fehlend")
