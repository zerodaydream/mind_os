import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings
from backend.common.database.models import Artifact, ArtifactChunk, ChunkEmbedding, Commitment
from backend.services.commitments.core.extract import extract_commitments
from backend.services.llm.core.provider_gateway import build_provider_gateway
from backend.services.retrieval.core.embed import embed_texts
from backend.services.memories.core.persist import add_new_memories_from_artifact
from backend.services.memories.core.rules import extract_memory_rules
from backend.services.tensions.core.detect import run_tension_detection
from backend.worker.outbox_say import outbox_say


async def _prune_duplicate_commitments(session: AsyncSession, user_id) -> None:
    await session.execute(
        text(
            """
            DELETE FROM commitments c
            USING commitments d
            WHERE c.user_id = :uid AND d.user_id = :uid
              AND lower(trim(c.summary)) = lower(trim(d.summary))
              AND c.id > d.id
            """
        ),
        {"uid": user_id},
    )


async def handle_artifact_process(session: AsyncSession, payload: dict) -> None:
    artifact_id = uuid.UUID(payload["artifact_id"])
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        return

    settings = get_settings()
    config = get_config()
    gateway = build_provider_gateway(settings) if config.is_enabled("llm") else None
    label = (artifact.title or artifact.external_id or str(artifact_id))[:64]

    result = await session.execute(
        select(ArtifactChunk).where(ArtifactChunk.artifact_id == artifact_id).order_by(ArtifactChunk.ord)
    )
    chunks = list(result.scalars().all())
    full_text = "\n\n".join(c.text for c in chunks)
    outbox_say(f"{label} · {len(chunks)} chunks")
    await _prune_duplicate_commitments(session, artifact.user_id)

    if config.is_enabled("retrieval") and chunks:
        texts = [c.text for c in chunks]
        outbox_say(f"{label} · embedding ({settings.embedding_model})")
        try:
            vectors = embed_texts(texts, settings.embedding_model)
            for chunk, vector in zip(chunks, vectors, strict=True):
                await session.merge(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        embedding_model=settings.embedding_model,
                        embedding=vector,
                    )
                )
            outbox_say(f"{label} · {len(vectors)} vectors saved")
        except RuntimeError as exc:
            outbox_say(f"{label} · embed skipped: {exc}")

    chunk_ids = [c.id for c in chunks]
    if chunk_ids:
        already = await session.execute(
            select(Commitment.id)
            .where(Commitment.source_chunk_id.in_(chunk_ids))
            .limit(1)
        )
        if already.scalar_one_or_none() is None:
            for draft in await extract_commitments(full_text, gateway=gateway):
                summary = (draft.get("summary") or "").strip()[:500]
                if not summary:
                    continue
                exists = await session.execute(
                    select(Commitment.id)
                    .where(
                        Commitment.user_id == artifact.user_id,
                        func.lower(func.trim(Commitment.summary)) == summary.lower(),
                    )
                    .limit(1)
                )
                if exists.scalar_one_or_none() is not None:
                    continue
                session.add(
                    Commitment(
                        user_id=artifact.user_id,
                        summary=summary,
                        actor=draft.get("actor"),
                        counterparty=draft.get("counterparty"),
                        due_at=draft.get("due_at"),
                        confidence=float(draft.get("confidence", 0.5)),
                        extraction_mode=draft.get("extraction_mode", "rules"),
                        source_chunk_id=chunks[0].id if chunks else None,
                    )
                )
    if config.is_enabled("memories"):
        drafts = extract_memory_rules(full_text)
        if drafts:
            await add_new_memories_from_artifact(
                session,
                user_id=artifact.user_id,
                artifact_id=artifact_id,
                drafts=drafts,
            )
    if config.is_enabled("tensions"):
        await run_tension_detection(session, artifact.user_id)