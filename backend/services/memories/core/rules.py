import re

_KINDS = (
    ("preference", re.compile(r"\b(I prefer|I'd rather|I like to)\b", re.I)),
    ("decision", re.compile(r"\b(I decided|we decided|decision:)\b", re.I)),
    ("fact", re.compile(r"\b(my (email|phone|address) is|I work at|I live in)\b", re.I)),
)


def extract_memory_rules(text: str) -> list[dict]:
    stripped = text.strip()
    if not stripped:
        return []
    out: list[dict] = []
    for kind, pat in _KINDS:
        if pat.search(stripped):
            out.append({"kind": kind, "content": stripped[:1000]})
    return out
