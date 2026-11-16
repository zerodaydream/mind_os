"""CSV attachment text."""

from __future__ import annotations

import csv
from io import StringIO


def extract_csv(raw: bytes) -> str:
    text = raw.decode("utf-8-sig", errors="replace")
    rows: list[str] = []
    for row in csv.reader(StringIO(text)):
        if row:
            rows.append("\t".join(row))
    return "\n".join(rows)