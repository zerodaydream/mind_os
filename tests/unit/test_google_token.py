import time

from backend.services.integrations.core.google_oauth import access_token_expired, normalize_token_response


def test_expiry_from_expires_in() -> None:
    tokens = normalize_token_response({"access_token": "x", "expires_in": 3600})
    assert "expires_at" in tokens
    assert not access_token_expired(tokens)


def test_expired_when_past() -> None:
    tokens = {"access_token": "x", "expires_at": int(time.time()) - 10}
    assert access_token_expired(tokens)
