"""HTML helper to finish OAuth in a browser popup via postMessage + window.close."""

from __future__ import annotations

import json

from fastapi.responses import HTMLResponse

OAUTH_MESSAGE_TYPE = "mindos-google-oauth"


def oauth_popup_close_response(
    *,
    ui_origin: str,
    status: str,
    detail: str = "",
) -> HTMLResponse:
    payload = {"type": OAUTH_MESSAGE_TYPE, "status": status, "detail": detail}
    payload_json = json.dumps(payload)
    target_origin = json.dumps(ui_origin or "*")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>MindOS — Gmail</title></head>
<body>
<p style="font-family: system-ui, sans-serif; padding: 1rem;">Finishing sign-in…</p>
<script>
(function () {{
  var payload = {payload_json};
  var target = {target_origin};
  try {{
    if (window.opener && !window.opener.closed) {{
      window.opener.postMessage(payload, target === "*" ? "*" : target);
    }}
  }} catch (e) {{}}
  window.close();
  setTimeout(function () {{
    document.body.textContent = payload.status === "connected"
      ? "Gmail connected. You can close this window."
      : "Sign-in did not complete. You can close this window.";
  }}, 400);
}})();
</script>
</body>
</html>"""
    return HTMLResponse(html)
