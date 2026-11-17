import asyncio

from cli import async_util


def test_cancel_active_turn_without_task_returns_false() -> None:
    async_util._active_turn_task = None
    assert async_util.cancel_active_turn() is False


async def _sleepy() -> int:
    await asyncio.sleep(60)
    return 1


def test_cancel_active_turn_signals_running_task() -> None:
    loop = asyncio.new_event_loop()
    try:
        async_util._shared_event_loop = loop
        task = loop.create_task(_sleepy())
        async_util._active_turn_task = task

        def _cancel() -> None:
            assert async_util.cancel_active_turn() is True

        loop.call_soon(_cancel)

        with __import__("pytest").raises(asyncio.CancelledError):
            loop.run_until_complete(task)
    finally:
        async_util._active_turn_task = None
        async_util._shared_event_loop = None
        loop.close()