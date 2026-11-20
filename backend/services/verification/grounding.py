"""Deterministic checks: cited eids, filenames, URLs, emails, amounts, dates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.services.agents.core.contracts.evidence import EvidencePack

_FILENAME = re.compile(
    r"\b[\w.-]+\.(?:pdf|docx?|xlsx?|csv|pptx?|png|jpe?g|gif|txt|md|zip)\b",
    re.I,
)
_URL = re.compile(r"https?://[^\s)\]>\"']+", re.I)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_AMOUNT = re.compile(r"(?<!\w)(?:USD|INR|Rs\.?|\$|€|£)\s?[\d,]+(?:\.\d{1,2})?(?!\w)", re.I)
_DATE_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CITATION = re.compile(r"\[(E\d+)\]")


@dataclass
class GroundingReport:
    """Result of a deterministic grounding pass."""

    ok: bool
    violations: list[str] = field(default_factory=list)
    cited_eids: list[str] = field(default_factory=list)


def verify_grounded_answer(
    answer: str,
    pack: EvidencePack,
    *,
    citations: list[str] | None = None,
) -> GroundingReport:
    """Require concrete claims to appear in cited evidence text."""
    violations: list[str] = []
    by_eid = pack.by_eid()
    cited = list(citations or [])
    cited.extend(_CITATION.findall(answer or ""))
    # Preserve order, unique
    seen: set[str] = set()
    cited_eids: list[str] = []
    for eid in cited:
        if eid not in seen:
            seen.add(eid)
            cited_eids.append(eid)

    for eid in cited_eids:
        if eid not in by_eid:
            violations.append(f"unknown_citation:{eid}")

    corpus = _evidence_corpus(pack, cited_eids)
    corpus_l = corpus.lower()

    for name in _FILENAME.findall(answer or ""):
        if name.lower() not in corpus_l and not _in_attachment_meta(pack, name):
            violations.append(f"invented_filename:{name}")

    for url in _URL.findall(answer or ""):
        if url.rstrip(".,;)").lower() not in corpus_l:
            violations.append(f"invented_url:{url}")

    for email in _EMAIL.findall(answer or ""):
        if email.lower() not in corpus_l:
            violations.append(f"invented_email:{email}")

    for amount in _AMOUNT.findall(answer or ""):
        norm = re.sub(r"\s+", "", amount).lower()
        if norm not in re.sub(r"\s+", "", corpus_l):
            violations.append(f"invented_amount:{amount}")

    for date in _DATE_ISO.findall(answer or ""):
        if date not in corpus:
            violations.append(f"invented_date:{date}")

    return GroundingReport(
        ok=not violations,
        violations=violations,
        cited_eids=cited_eids,
    )


def _evidence_corpus(pack: EvidencePack, cited_eids: list[str]) -> str:
    if cited_eids:
        parts = []
        by_eid = pack.by_eid()
        for eid in cited_eids:
            ev = by_eid.get(eid)
            if ev:
                parts.append(f"{ev.title or ''}\n{ev.text}\n{ev.uri or ''}")
                for v in (ev.metadata or {}).values():
                    if isinstance(v, str):
                        parts.append(v)
                    elif isinstance(v, list):
                        parts.extend(str(x) for x in v)
        if parts:
            return "\n".join(parts)
    # Fallback: all evidence (strict mode still catches missing eids separately)
    parts = []
    for ev in pack.items:
        parts.append(f"{ev.title or ''}\n{ev.text}\n{ev.uri or ''}")
        for v in (ev.metadata or {}).values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(x) for x in v)
    return "\n".join(parts)


def _in_attachment_meta(pack: EvidencePack, name: str) -> bool:
    needle = name.lower()
    for ev in pack.items:
        names = (ev.metadata or {}).get("attachment_names") or []
        if isinstance(names, list) and any(str(n).lower() == needle for n in names):
            return True
        if needle in (ev.title or "").lower():
            return True
    return False
