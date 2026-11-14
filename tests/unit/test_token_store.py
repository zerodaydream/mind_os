from backend.services.integrations.core.token_store import decrypt_tokens, encrypt_tokens


def test_token_roundtrip() -> None:
    key = "test-secret-key-32-chars-minimum!!"
    blob = encrypt_tokens(key, {"access_token": "abc", "refresh_token": "def"})
    out = decrypt_tokens(key, blob)
    assert out["access_token"] == "abc"
