from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_repl_loop: asyncio.AbstractEventLoop | None = None


def _sync_drop_engine() -> None:
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


async def _prepare() -> None:
    from backend.common.ssl_env import fix_ssl_cert_env
    from backend.common.database import lifecycle
    from backend.worker.bootstrap import bootstrap

    fix_ssl_cert_env()
    if lifecycle._engine is None:
        bootstrap()


def start_repl_loop() -> None:
    global _repl_loop
    _sync_drop_engine()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _repl_loop = loop
    loop.run_until_complete(_prepare())


def stop_repl_loop() -> None:
    global _repl_loop
    if _repl_loop is None or _repl_loop.is_closed():
        _repl_loop = None
        return

    async def _shutdown() -> None:
        from backend.common.database.lifecycle import dispose_db

        await dispose_db()

    try:
        _repl_loop.run_until_complete(_shutdown())
    except Exception:
        _sync_drop_engine()
    finally:
        _repl_loop.close()
        _repl_loop = None


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    async def _run() -> T:
        await _prepare()
        return await coro

    if _repl_loop is not None and not _repl_loop.is_closed():
        return _repl_loop.run_until_complete(_run())

    _sync_drop_engine()

    async def _one_shot() -> T:
        await _prepare()
        try:
            return await coro
        finally:
            from backend.common.database.lifecycle import dispose_db

            await dispose_db()

    return asyncio.run(_one_shot())
