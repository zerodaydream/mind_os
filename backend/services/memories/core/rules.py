import re

_KINDS = (
    ("preference", re.compile(r"\b(I prefer|I'd rather|I like to)\b", re.I)),
    ("decision", re.compile(r"\b(I decided|we decided|decision:)\b", re.I)),
    ("fact", re.compile(r"\b(my (email|phone|address) is|I work at|I live in)\b", re.I)),
)
_MAX_CONTENT = 500


def _sentence_around(text: str, start: int, end: int) -> str:
    """Return the sentence spanning [start, end), not the whole artifact."""
    left = 0
    for m in re.finditer(r"[.!?]\s+|\n+", text[:start]):
        left = m.end()
    right = len(text)
    m = re.search(r"[.!?](?:\s|$)|$", text[end:])
    if m is not None:
        right = end + m.end()
    sentence = text[left:right].strip()
    if not sentence:
        sentence = text[start:end].strip()
    return sentence[:_MAX_CONTENT]


def extract_memory_rules(text: str) -> list[dict]:
    """Extract atomic memory candidates from first-person statements.

    Doc-derived hits are proposed (not auto-approved). Content is the matching
    sentence only — never a 1000-char artifact slice.
    """
    stripped = text.strip()
    if not stripped:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, pat in _KINDS:
        for match in pat.finditer(stripped):
            content = _sentence_around(stripped, match.start(), match.end())
            if not content:
                continue
            key = (kind, content.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "kind": kind,
                    "content": content,
                    "approved": False,
                    "origin": "doc_derived",
                }
            )
    return out
