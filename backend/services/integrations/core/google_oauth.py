import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import httpx

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [GMAIL_READONLY, CALENDAR_EVENTS]


def scopes_from_token_response(raw: dict) -> list[str]:
    scope = raw.get("scope")
    if isinstance(scope, str) and scope.strip():
        return scope.split()
    return list(SCOPES)


def auth_url(settings, *, state: str) -> str:
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "include_granted_scopes": "true",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def make_state(secret_key: str, user_id: str, *, popup: bool = False) -> str:
    payload = json.dumps({"uid": user_id, "ts": int(time.time()), "popup": popup})
    sig = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def verify_state(secret_key: str, state: str) -> dict | None:
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        payload, sig = decoded.rsplit("|", 1)
        expect = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(payload)
        if time.time() - data["ts"] > 600:
            return None
        return {"uid": data["uid"], "popup": bool(data.get("popup"))}
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def normalize_token_response(raw: dict) -> dict:
    out = dict(raw)
    if "expires_in" in out and "expires_at" not in out:
        out["expires_at"] = int(time.time()) + int(out["expires_in"])
    return out


def access_token_expired(tokens: dict, *, skew_sec: int = 60) -> bool:
    exp = tokens.get("expires_at")
    if exp is None:
        return False
    return time.time() >= int(exp) - skew_sec


async def exchange_code(settings, code: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return normalize_token_response(resp.json())


async def refresh_access_token(settings, tokens: dict) -> dict:
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise ValueError("integration missing refresh_token; reconnect Google OAuth")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        merged = {**tokens, **normalize_token_response(resp.json())}
        if "refresh_token" not in merged and refresh:
            merged["refresh_token"] = refresh
        return merged
