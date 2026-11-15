import pytest

from backend.common.config_loader.settings import Settings
from backend.services.integrations.core.calendar.timezone import (
    detect_client_iana_timezone,
    effective_calendar_tz,
    resolve_user_timezone,
    timezone_from_message,
)


@pytest.mark.asyncio
async def test_resolve_order_message_then_client() -> None:
    class _User:
        id = "u"

    tz = await resolve_user_timezone(
        None,
        user=_User(),
        settings=Settings(mindos_default_timezone=""),
        message="meeting 3pm IST",
        client_tz="America/New_York",
    )
    assert tz == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_resolve_client_before_env() -> None:
    class _User:
        id = "u"

    tz = await resolve_user_timezone(
        None,  # type: ignore[arg-type]
        user=_User(),
        settings=Settings(mindos_default_timezone="Europe/London"),
        message="tomorrow 1pm",
        client_tz="Asia/Kolkata",
    )
    assert tz == "Asia/Kolkata"


def test_timezone_from_message_none_when_unspecified() -> None:
    assert timezone_from_message("tomorrow 1pm") is None


def test_effective_calendar_tz_replaces_model_utc() -> None:
    assert effective_calendar_tz("UTC", "Asia/Kolkata") == "Asia/Kolkata"
    assert effective_calendar_tz("", "Asia/Kolkata") == "Asia/Kolkata"
    assert effective_calendar_tz("America/Chicago", "Asia/Kolkata") == "America/Chicago"


def test_detect_client_iana_resolves_iana() -> None:
    """macOS can expose fixed-offset IST without ZoneInfo.key; readlink still yields IANA."""
    tz = detect_client_iana_timezone()
    assert tz == "Asia/Kolkata"