from backend.services.integrations.core.calendar.scheduling import (
    collect_calendar_thread,
    expected_tomorrow_date,
    parse_wall_clock,
    resolve_event_start,
    user_message_has_wall_time,
    validate_create_schedule,
)


def test_no_time_blocks_create() -> None:
    err = validate_create_schedule(
        task_message="reminder for tomorrow",
        start_iso="2026-11-16T13:00:00",
        all_day=False,
        tz="Asia/Kolkata",
    )
    assert err is not None
    assert "did not specify a time" in err


def test_wrong_tomorrow_date() -> None:
    err = validate_create_schedule(
        task_message="meeting tomorrow at 1pm",
        start_iso="2026-11-20T13:00:00",
        all_day=False,
        tz="Asia/Kolkata",
    )
    assert err is not None
    assert "not tomorrow" in err


def test_wall_time_detected() -> None:
    assert user_message_has_wall_time("tomorrow at 1 pm")
    assert user_message_has_wall_time("Set it up for Four p m")
    assert not user_message_has_wall_time("reminder for tomorrow")


def test_parse_four_pm_variants() -> None:
    assert parse_wall_clock("four pm") == (16, 0)
    assert parse_wall_clock("4 pm") == (16, 0)
    assert parse_wall_clock("Four p m") == (16, 0)


def test_collect_calendar_thread() -> None:
    hist = [
        {"role": "user", "content": "Set up a calendar meeting for tomorrow And name it online meet"},
        {"role": "assistant", "content": "What time tomorrow?"},
        {"role": "user", "content": "Set it up for Four p m."},
    ]
    merged = collect_calendar_thread(hist, "done")
    assert "tomorrow" in merged
    assert "four" in merged.lower() or "4" in merged


def test_resolve_event_start_multiturn() -> None:
    task = (
        "Set up a calendar meeting for tomorrow And name it online meet\n"
        "Set it up for 4 pm"
    )
    tz = "Asia/Kolkata"
    exp = expected_tomorrow_date(tz)
    iso = resolve_event_start(task, tz)
    assert iso is not None
    assert iso.startswith(f"{exp}T16:00:00")


def test_expected_tomorrow_format() -> None:
    d = expected_tomorrow_date("Asia/Kolkata")
    assert len(d) == 10 and d[4] == "-"


def test_multiturn_tomorrow_guard() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = "Asia/Kolkata"
    exp = expected_tomorrow_date(tz)
    today = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
    assert today != exp
    err = validate_create_schedule(
        task_message=(
            "calendar reminder for tomorrow\nfor 3pm, name it important meet"
        ),
        start_iso=f"{today}T15:00:00",
        all_day=False,
        tz=tz,
    )
    assert err is not None
    assert "not tomorrow" in err


def test_utc_offset_blocked_for_local_tz() -> None:
    exp = expected_tomorrow_date("Asia/Kolkata")
    err = validate_create_schedule(
        task_message="tomorrow at 3pm",
        start_iso=f"{exp}T15:00:00Z",
        all_day=False,
        tz="Asia/Kolkata",
    )
    assert err is not None
    assert "naive local" in err