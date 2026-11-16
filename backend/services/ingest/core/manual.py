import hashlib
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import Artifact, ArtifactChunk, User
from backend.common.database.outbox import enqueue_artifact_process
from backend.services.ingest.core.chunker import chunk_text


async def ensure_default_user(session: AsyncSession, settings: Settings) -> User:
    email = settings.mindos_default_user_email
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        return user
    user = User(email=email, password_hash="!")
    session.add(user)
    await session.flush()
    return user


async def ingest_manual_note(
    session: AsyncSession,
    *,
    user: User,
    title: str | None,
    body: str,
    settings: Settings,
    source: str = "manual",
    external_id: str | None = None,
    metadata: dict | None = None,
    occurred_at: datetime | None = None,
) -> Artifact:
    content_hash = hashlib.sha256(body.encode()).hexdigest()

    artifact = Artifact(
        user_id=user.id,
        source=source,
        external_id=external_id or content_hash[:32],
        title=title,
        content_hash=content_hash,
        metadata_=metadata or {},
        occurred_at=occurred_at,
    )
    session.add(artifact)
    await session.flush()

    version = settings.chunker_version
    for ord_, chunk_body in chunk_text(
        body, version=version, embedding_model=settings.embedding_model
    ):
        from backend.services.retrieval.core.embed import embed_token_count

        session.add(
            ArtifactChunk(
                artifact_id=artifact.id,
                ord=ord_,
                text=chunk_body,
                token_count=embed_token_count(
                    chunk_body, model_id=settings.embedding_model, is_query=False
                ),
                chunker_version=version,
            )
        )
    await session.execute(
        text("UPDATE artifacts SET tsv = to_tsvector('english', :body) WHERE id = :id"),
        {"body": body, "id": artifact.id},
    )
    await enqueue_artifact_process(session, str(artifact.id))
    await session.flush()
    return artifact
