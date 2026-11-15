import base64
import hashlib
import json

from cryptography.fernet import Fernet


def _fernet(secret_key: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    return Fernet(key)


def encrypt_tokens(secret_key: str, tokens: dict) -> bytes:
    return _fernet(secret_key).encrypt(json.dumps(tokens).encode())


def decrypt_tokens(secret_key: str, blob: bytes) -> dict:
    return json.loads(_fernet(secret_key).decrypt(blob).decode())
