"""Adapter: TurnRetrieval → EvidencePack when the RRF pipeline did not run."""

from __future__ import annotations

from backend.services.agents.core.contracts.evidence import Evidence, EvidencePack
from backend.services.agents.core.turn.turn_retrieval import TurnRetrieval


def evidence_pack_from_turn_retrieval(ctx: TurnRetrieval) -> EvidencePack:
    """Map prefetch rows into typed Evidence with stable eids (E1…)."""
    items: list[Evidence] = []
    n = 0

    for row in ctx.gmail_ctx or []:
        n += 1
        eid = f"E{n}"
        title = row.get("title") or "(no subject)"
        preview = (row.get("preview") or row.get("body") or "").strip()
        sender = row.get("from_addr") or ""
        text = f"From: {sender}\n{preview}".strip()
        names = row.get("attachment_names") or []
        if not names and row.get("attachments"):
            names = [
                a.get("filename") or a.get("name")
                for a in (row.get("attachments") or [])
                if isinstance(a, dict)
            ]
        items.append(
            Evidence(
                eid=eid,
                kind="mail",
                text=text,
                score=float(row.get("score") or 0.0),
                artifact_id=str(row.get("artifact_id") or "") or None,
                title=str(title),
                uri=row.get("drive_url") or row.get("uri"),
                metadata={
                    "from_addr": sender,
                    "attachment_names": [x for x in names if x],
                },
            )
        )

    for hit in ctx.results or []:
        n += 1
        eid = f"E{n}"
        title = hit.get("title") or hit.get("artifact_id") or "artifact"
        snippet = (hit.get("snippet") or hit.get("text") or "").strip()
        items.append(
            Evidence(
                eid=eid,
                kind=str(hit.get("source") or "artifact"),
                text=snippet,
                score=float(hit.get("score") or 0.0),
                artifact_id=str(hit.get("artifact_id") or "") or None,
                title=str(title),
                uri=hit.get("uri"),
                metadata={},
            )
        )

    for line in ctx.commitments or []:
        n += 1
        items.append(
            Evidence(
                eid=f"E{n}",
                kind="commitment",
                text=str(line),
                title="commitment",
            )
        )

    for line in ctx.memories or []:
        n += 1
        items.append(
            Evidence(
                eid=f"E{n}",
                kind="memory",
                text=str(line),
                title="memory",
            )
        )

    return EvidencePack(
        items=items,
        insufficient=not bool(items),
        queries=[ctx.message] if ctx.message else [],
    )
