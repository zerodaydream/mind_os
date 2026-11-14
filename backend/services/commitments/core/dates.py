import re
from datetime import datetime, timezone

_BY = re.compile(r"\bby\s+(20\d{2}-\d{2}-\d{2})\b", re.I)
_DUE = re.compile(r"\bdue\s+(20\d{2}-\d{2}-\d{2})\b", re.I)
_ISO = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def parse_due_at(text: str) -> datetime | None:
    for pat in (_BY, _DUE, _ISO):
        m = pat.search(text)
        if m:
            day = datetime.strptime(m.group(1), "%Y-%m-%d")
            return day.replace(tzinfo=timezone.utc)
    return None
