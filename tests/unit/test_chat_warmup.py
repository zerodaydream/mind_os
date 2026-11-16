import time

import cli.chat_cmd as chat_cmd
from cli.async_util import shared_event_loop_is_running


def test_take_chat_warmup_result_drains_once(monkeypatch) -> None:
    sentinel = object()
    with chat_cmd._chat_warmup_lock:
        chat_cmd._chat_warmup_result = sentinel
    assert chat_cmd._take_chat_warmup_result() == sentinel
    assert chat_cmd._take_chat_warmup_result() is None


def test_start_chat_warmup_populates_result(monkeypatch) -> None:
    with chat_cmd._chat_warmup_lock:
        chat_cmd._chat_warmup_result = None
        chat_cmd._chat_warmup_thread = None

    def fake_boot(*args, **kwargs):
        return ({}, None, [], "New Chat")

    monkeypatch.setattr(chat_cmd, "prepare_chat_session_state", fake_boot)

    chat_cmd.start_chat_warmup()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with chat_cmd._chat_warmup_lock:
            if chat_cmd._chat_warmup_result is not None:
                break
        time.sleep(0.01)
    assert chat_cmd._take_chat_warmup_result() is not None


def test_chat_warmup_restarts_after_dead_thread(monkeypatch) -> None:
    with chat_cmd._chat_warmup_lock:
        chat_cmd._chat_warmup_result = None
        chat_cmd._chat_warmup_thread = None

    calls = {"n": 0}

    def fake_boot(*args, **kwargs):
        calls["n"] += 1
        return ({}, None, [], "New Chat")

    monkeypatch.setattr(chat_cmd, "prepare_chat_session_state", fake_boot)

    chat_cmd.start_chat_warmup()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with chat_cmd._chat_warmup_lock:
            if chat_cmd._chat_warmup_result is not None:
                break
        time.sleep(0.01)
    chat_cmd._take_chat_warmup_result()
    chat_cmd.start_chat_warmup()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with chat_cmd._chat_warmup_lock:
            if chat_cmd._chat_warmup_result is not None:
                break
        time.sleep(0.01)

    assert chat_cmd._take_chat_warmup_result() is not None


def test_shared_event_loop_is_running_false_by_default() -> None:
    assert shared_event_loop_is_running() is False