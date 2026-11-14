from __future__ import annotations

from cli import term
from backend.common.config_loader.settings import get_settings


def cmd_schedule() -> int:
    settings = get_settings()
    days = settings.mindos_sync_interval_days
    term.banner()
    term.step("Interval", f"every {days:g} day(s)  (MINDOS_SYNC_INTERVAL_DAYS)")
    cron = f"0 9 */{max(1, int(days))} * * mindos sync --gmail --process"
    term.note(
        f"Add to crontab -e (requires global mindos + MINDOS_HOME in shell profile):\n\n  {cron}\n"
    )
    return 0
