import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings
from backend.common.database.models import Artifact, ArtifactChunk, ChunkEmbedding, Commitment
from backend.services.commitments.core.extract import extract_commitments
from backend.services.llm.core.gateway import build_llm_gateway
from backend.services.retrieval.core.embed import embed_texts
from backend.services.memories.core.persist import upsert_memories_from_artifact
from backend.services.memories.core.rules import extract_memory_rules
from backend.services.tensions.core.detect import run_tension_detection


async def handle_artifact_process(session: AsyncSession, payload: dict) -> None:
    artifact_id = uuid.UUID(payload["artifact_id"])
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        return

    settings = get_settings()
    config = get_config()
    gateway = build_llm_gateway(settings) if config.is_enabled("llm") else None

    result = await session.execute(
        select(ArtifactChunk).where(ArtifactChunk.artifact_id == artifact_id).order_by(ArtifactChunk.ord)
    )
    chunks = list(result.scalars().all())
    full_text = "\n\n".join(c.text for c in chunks)

    if config.is_enabled("retrieval"):
        texts = [c.text for c in chunks]
        try:
            vectors = embed_texts(texts, settings.embedding_model)
            for chunk, vector in zip(chunks, vectors, strict=True):
                session.merge(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        embedding_model=settings.embedding_model,
                        embedding=vector,
                    )
                )
        except RuntimeError:
            pass

    for draft in await extract_commitments(full_text, gateway=gateway):
        session.add(
            Commitment(
                user_id=artifact.user_id,
                summary=draft["summary"],
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
            await upsert_memories_from_artifact(
                session,
                user_id=artifact.user_id,
                artifact_id=artifact_id,
                drafts=drafts,
            )
    if config.is_enabled("tensions"):
        await run_tension_detection(session, artifact.user_id)
    await session.flush()
