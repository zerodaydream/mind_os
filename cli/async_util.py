"""Reuse one asyncio event loop for the interactive CLI REPL and nested commands."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_shared_event_loop: asyncio.AbstractEventLoop | None = None


def shared_event_loop_is_running() -> bool:
    """True when the REPL (or a parent command) already owns the shared loop."""
    return _shared_event_loop is not None and not _shared_event_loop.is_closed()


def _dispose_db_engine_sync() -> None:
    """Drop a pool bound to a dead loop without awaiting async close."""
    from backend.common.database import lifecycle

    if lifecycle._engine is None:
        return
    try:
        lifecycle._engine.sync_engine.dispose()
    except Exception:
        pass
    lifecycle._engine = None
    lifecycle._session_factory = None


async def _ensure_cli_backend_ready() -> None:
    """SSL env, then worker/DB bootstrap if the shared loop has no engine yet."""
    from backend.common.ssl_env import fix_ssl_cert_env
    from backend.common.database import lifecycle
    from backend.worker.bootstrap import bootstrap

    fix_ssl_cert_env()
    if lifecycle._engine is None:
        bootstrap()


def start_shared_event_loop() -> None:
    """Create and retain the CLI event loop until ``stop_shared_event_loop``."""
    global _shared_event_loop
    _dispose_db_engine_sync()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _shared_event_loop = loop
    loop.run_until_complete(_ensure_cli_backend_ready())


def stop_shared_event_loop() -> None:
    """Close the shared loop and dispose the async DB pool."""
    global _shared_event_loop
    if _shared_event_loop is None or _shared_event_loop.is_closed():
        _shared_event_loop = None
        return

    async def _shutdown() -> None:
        from backend.common.database.lifecycle import dispose_db

        await dispose_db()

    try:
        _shared_event_loop.run_until_complete(_shutdown())
    except Exception:
        _dispose_db_engine_sync()
    finally:
        _shared_event_loop.close()
        _shared_event_loop = None


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run *coro* on the shared loop, or a one-shot ``asyncio.run`` with teardown."""
    async def _run() -> T:
        await _ensure_cli_backend_ready()
        return await coro

    if _shared_event_loop is not None and not _shared_event_loop.is_closed():
        return _shared_event_loop.run_until_complete(_run())

    _dispose_db_engine_sync()

    async def _one_shot() -> T:
        await _ensure_cli_backend_ready()
        try:
            return await coro
        finally:
            from backend.common.database.lifecycle import dispose_db

            await dispose_db()

    return asyncio.run(_one_shot())