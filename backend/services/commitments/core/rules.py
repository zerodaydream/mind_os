import re

from backend.common.domain.commitment import CommitmentDraft
from backend.services.commitments.core.dates import parse_due_at

_WILL = re.compile(r"\b(I will|I'll|I owe|promised to)\b", re.I)


def extract_rules(text: str) -> list[CommitmentDraft]:
    if not _WILL.search(text):
        return []
    summary = text.strip()[:500]
    return [
        CommitmentDraft(
            summary=summary,
            actor="me",
            due_at=parse_due_at(text),
            confidence=0.55,
            extraction_mode="rules",
        )
    ]
