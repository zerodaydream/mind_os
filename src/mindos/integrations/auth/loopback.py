"""The loopback redirect: a one-shot HTTP listener on 127.0.0.1 that receives the OAuth code.

Bound to the loopback interface only, on a port the OS picks. It accepts exactly one request that
carries the expected `state`; anything else is answered 400 and ignored, so a stray local request
cannot complete or break a sign-in.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_PAGE = (
    "<!doctype html><meta charset=utf-8><title>MindOS</title>"
    "<body style='font-family:system-ui;margin:4em'><h2>{title}</h2><p>{body}</p>"
)


class LoopbackError(Exception):
    """The sign-in did not complete. The message is safe to show."""


@dataclass(frozen=True)
class CallbackResult:
    code: str


class LoopbackReceiver:
    def __init__(self, expected_state: str) -> None:
        self._state = expected_state
        self._result: asyncio.Future[CallbackResult] = asyncio.get_running_loop().create_future()
        self._server: asyncio.Server | None = None

    async def start(self) -> str:
        """Begin listening. Returns the redirect URI to give the provider."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def wait(self, timeout_s: float) -> CallbackResult:
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), timeout_s)
        except TimeoutError as exc:
            raise LoopbackError("Timed out waiting for the browser sign-in.") from exc

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 5)).decode("latin-1")
            parts = line.split()
            target = parts[1] if len(parts) >= 2 and parts[0] == "GET" else ""
            status, title, body = self._judge(parse_qs(urlsplit(target).query))
            payload = _PAGE.format(title=title, body=body).encode()
            head = f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\nContent-Length: {len(payload)}"
            writer.write(f"{head}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        except (TimeoutError, ConnectionError, UnicodeError):
            pass
        finally:
            writer.close()

    def _judge(self, query: dict[str, list[str]]) -> tuple[str, str, str]:
        if self._result.done() or query.get("state", [""])[0] != self._state:
            return "400 Bad Request", "Not accepted", "This request was not part of the sign-in."
        if "error" in query:
            reason = query["error"][0][:80]
            self._result.set_exception(
                LoopbackError(f"The provider refused the sign-in: {reason}.")
            )
            return "200 OK", "Sign-in was not completed", "You can close this tab."
        code = query.get("code", [""])[0]
        if not code:
            return "400 Bad Request", "Not accepted", "The sign-in carried no code."
        self._result.set_result(CallbackResult(code))
        return "200 OK", "Signed in", "You can close this tab and return to the terminal."
