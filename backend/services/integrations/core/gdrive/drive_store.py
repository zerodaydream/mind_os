"""Gmail attachment blobs on GDrive (REST API + OAuth token files)."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx

_cached_ingest_root_id: str | None = None
_ingest_type_folder_ids: dict[str, str] = {}
_ingest_root_lock = asyncio.Lock()
_ingest_type_lock = asyncio.Lock()
_DEFAULT_INGEST_FOLDER = "MindOS Gmail Attachments"


def _attachment_type_folder(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext if ext else "other"


def _env(primary: str, legacy: str) -> str:
    val = (os.environ.get(primary) or "").strip()
    if val:
        return val
    return (os.environ.get(legacy) or "").strip()


def gdrive_ingest_configured() -> bool:
    oauth = _env("GDRIVE_OAUTH_PATH", "GDRIVE_MCP_OAUTH_PATH")
    tokens = _env("GDRIVE_TOKEN_PATH", "GDRIVE_MCP_TOKEN_PATH")
    return bool(oauth and tokens and Path(oauth).expanduser().is_file())


def _ingest_folder_display_name() -> str:
    raw = _env("GDRIVE_INGEST_FOLDER", "GDRIVE_MCP_INGEST_FOLDER") or _DEFAULT_INGEST_FOLDER
    return raw.strip() or _DEFAULT_INGEST_FOLDER


def _oauth_installed() -> dict[str, str]:
    path = _env("GDRIVE_OAUTH_PATH", "GDRIVE_MCP_OAUTH_PATH")
    if not path:
        raise RuntimeError("GDRIVE_OAUTH_PATH not set")
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    block = raw.get("installed") or raw.get("web") or {}
    client_id = block.get("client_id") or block.get("clientId")
    client_secret = block.get("client_secret") or block.get("clientSecret")
    if not client_id or not client_secret:
        raise RuntimeError("gdrive oauth json missing client_id/client_secret")
    return {"client_id": str(client_id), "client_secret": str(client_secret)}


def _bearer_token() -> str:
    token_path = _env("GDRIVE_TOKEN_PATH", "GDRIVE_MCP_TOKEN_PATH")
    if not token_path:
        raise RuntimeError("GDRIVE_TOKEN_PATH not set")
    path = Path(token_path).expanduser()
    tokens = json.loads(path.read_text(encoding="utf-8"))
    exp = int(tokens.get("expiry_date") or 0)
    if time.time() * 1000 < exp - 60_000:
        token = tokens.get("access_token")
        if token:
            return str(token)
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise RuntimeError("gdrive tokens missing refresh_token; re-authorize Drive OAuth")
    oauth = _oauth_installed()
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    merged = {**tokens, **resp.json()}
    if "refresh_token" not in merged:
        merged["refresh_token"] = refresh
    if "expires_in" in merged:
        merged["expiry_date"] = int(time.time() * 1000) + int(merged["expires_in"]) * 1000
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    access = merged.get("access_token")
    if not access:
        raise RuntimeError("token refresh missing access_token")
    return str(access)


async def _ingest_folder_id() -> str:
    global _cached_ingest_root_id  # noqa: PLW0603
    if _cached_ingest_root_id:
        return _cached_ingest_root_id
    async with _ingest_root_lock:
        if _cached_ingest_root_id:
            return _cached_ingest_root_id
        name = _ingest_folder_display_name()
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = quote(
            " and ".join(
                (
                    f"name = '{safe}'",
                    "mimeType = 'application/vnd.google-apps.folder'",
                    "'root' in parents",
                    "trashed = false",
                )
            )
        )
        token = _bearer_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            listed = await client.get(
                f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)&pageSize=1",
                headers=headers,
            )
            listed.raise_for_status()
            files = listed.json().get("files") or []
            if files:
                _cached_ingest_root_id = str(files[0]["id"])
                return _cached_ingest_root_id
            created = await client.post(
                "https://www.googleapis.com/drive/v3/files",
                headers={**headers, "Content-Type": "application/json"},
                json={"name": name, "mimeType": "application/vnd.google-apps.folder"},
            )
            created.raise_for_status()
            fid = created.json().get("id")
            if not fid:
                raise ValueError("drive folder create missing id")
            _cached_ingest_root_id = str(fid)
            return _cached_ingest_root_id


async def _ingest_type_folder_id(parent_id: str, type_key: str) -> str:
    global _ingest_type_folder_ids  # noqa: PLW0603
    cached = _ingest_type_folder_ids.get(type_key)
    if cached:
        return cached
    async with _ingest_type_lock:
        cached = _ingest_type_folder_ids.get(type_key)
        if cached:
            return cached
        safe = type_key.replace("\\", "\\\\").replace("'", "\\'")
        q = quote(
            " and ".join(
                (
                    f"name = '{safe}'",
                    "mimeType = 'application/vnd.google-apps.folder'",
                    f"'{parent_id}' in parents",
                    "trashed = false",
                )
            )
        )
        token = _bearer_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            listed = await client.get(
                f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)&pageSize=1",
                headers=headers,
            )
            listed.raise_for_status()
            files = listed.json().get("files") or []
            if files:
                fid = str(files[0]["id"])
            else:
                created = await client.post(
                    "https://www.googleapis.com/drive/v3/files",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "name": type_key,
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [parent_id],
                    },
                )
                created.raise_for_status()
                raw_id = created.json().get("id")
                if not raw_id:
                    raise ValueError("drive type folder create missing id")
                fid = str(raw_id)
        _ingest_type_folder_ids[type_key] = fid
        return fid


async def trash_gdrive_ingest_roots() -> int:
    """Trash every My Drive root folder matching ``GDRIVE_INGEST_FOLDER`` (subtree included)."""
    if not gdrive_ingest_configured():
        return 0
    global _cached_ingest_root_id, _ingest_type_folder_ids  # noqa: PLW0603
    name = _ingest_folder_display_name()
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = " and ".join(
        (
            f"name = '{safe}'",
            "mimeType = 'application/vnd.google-apps.folder'",
            "'root' in parents",
            "trashed = false",
        )
    )
    token = _bearer_token()
    headers = {"Authorization": f"Bearer {token}"}
    removed = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        page_token: str | None = None
        while True:
            params: dict = {"q": q, "fields": "nextPageToken,files(id)", "pageSize": 50}
            if page_token:
                params["pageToken"] = page_token
            listed = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                headers=headers,
            )
            listed.raise_for_status()
            body = listed.json()
            for item in body.get("files") or []:
                fid = item.get("id")
                if not fid:
                    continue
                resp = await client.delete(
                    f"https://www.googleapis.com/drive/v3/files/{fid}",
                    headers=headers,
                )
                resp.raise_for_status()
                removed += 1
            page_token = body.get("nextPageToken")
            if not page_token:
                break
    _cached_ingest_root_id = None
    _ingest_type_folder_ids.clear()
    return removed


async def drive_upload_bytes(*, name: str, mime_type: str, data: bytes) -> str:
    root = await _ingest_folder_id()
    type_key = _attachment_type_folder(name)
    parent = await _ingest_type_folder_id(root, type_key)
    boundary = "mindos_drive_boundary"
    meta = json.dumps(
        {
            "name": name,
            "mimeType": mime_type or "application/octet-stream",
            "parents": [parent],
        }
    ).encode()
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode() + meta + (
        f"\r\n--{boundary}\r\n"
        f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    token = _bearer_token()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
            content=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
        )
        resp.raise_for_status()
    file_id = resp.json().get("id")
    if not file_id:
        raise ValueError("drive upload missing file id")
    return str(file_id)


async def drive_download_bytes(file_id: str) -> bytes:
    token = _bearer_token()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
    return resp.content