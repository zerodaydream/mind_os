"""The user's time zone. Time handling lives in code, never in the model (ARCHITECTURE 12.4)."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_timezone(name: str | None, *, localtime: Path = Path("/etc/localtime")) -> ZoneInfo:
    """`name` if given; else the system zone from /etc/localtime; else UTC.

    Raises `ValueError` for an unknown explicit name.
    """
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                f"Unknown time zone '{name}'. Use an IANA name like Asia/Kolkata."
            ) from None
    try:
        target = str(localtime.resolve())
        if "zoneinfo/" in target:
            return ZoneInfo(target.split("zoneinfo/", 1)[1])
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    return ZoneInfo("UTC")
