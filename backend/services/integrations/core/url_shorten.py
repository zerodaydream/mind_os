"""Optional TinyURL shorten for user-facing links."""

from __future__ import annotations

import httpx


async def shorten_url(url: str) -> str:
    url = (url or "").strip()
    if not url or "tinyurl.com" in url:
        return url
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                "https://tinyurl.com/api-create.php",
                params={"url": url},
            )
            text = (resp.text or "").strip()
            if resp.status_code == 200 and text.startswith("http"):
                return text
    except Exception:
        pass
    return url