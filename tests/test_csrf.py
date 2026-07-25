import pytest
from fastapi import HTTPException

from app.csrf import get_or_create_csrf_token, verify_csrf
from tests.conftest import raw_post, register


class _FakeRequest:
    def __init__(self, session=None):
        self.session = session if session is not None else {}


def test_get_or_create_csrf_token_creates_and_reuses_a_token():
    request = _FakeRequest()
    token = get_or_create_csrf_token(request)

    assert token
    assert get_or_create_csrf_token(request) == token


def test_verify_csrf_accepts_a_matching_token():
    request = _FakeRequest(session={"csrf_token": "abc"})
    verify_csrf(request, "abc")  # must not raise


def test_verify_csrf_rejects_a_mismatched_token():
    request = _FakeRequest(session={"csrf_token": "abc"})
    with pytest.raises(HTTPException) as exc_info:
        verify_csrf(request, "wrong")
    assert exc_info.value.status_code == 403


def test_verify_csrf_rejects_a_missing_token():
    request = _FakeRequest(session={"csrf_token": "abc"})
    with pytest.raises(HTTPException) as exc_info:
        verify_csrf(request, None)
    assert exc_info.value.status_code == 403


def test_verify_csrf_rejects_when_the_session_has_no_token_at_all():
    request = _FakeRequest(session={})
    with pytest.raises(HTTPException):
        verify_csrf(request, "anything")


def test_post_without_csrf_token_is_rejected(client):
    register(client, "alice@example.com")
    resp = raw_post(client, "/watchlist/category/military/toggle", data={})
    assert resp.status_code == 403


def test_post_with_wrong_csrf_token_is_rejected(client):
    register(client, "alice@example.com")
    resp = raw_post(client, "/watchlist/category/military/toggle", data={"csrf_token": "not-the-real-token"})
    assert resp.status_code == 403


def test_post_with_correct_csrf_token_succeeds(client):
    register(client, "alice@example.com")
    resp = client.post("/watchlist/category/military/toggle", follow_redirects=False)
    assert resp.status_code == 303


def test_register_without_csrf_token_is_rejected(client):
    resp = raw_post(
        client,
        "/register",
        data={"email": "csrf-test@example.com", "password": "testpassword1", "password_confirm": "testpassword1"},
    )
    assert resp.status_code == 403
