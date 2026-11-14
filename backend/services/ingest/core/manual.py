import hashlib
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import Artifact, ArtifactChunk, User
from backend.common.database.outbox import enqueue
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
) -> Artifact:
    content_hash = hashlib.sha256(body.encode()).hexdigest()
    vault = Path(settings.mindos_vault_path)
    vault.mkdir(parents=True, exist_ok=True)
    rel = f"{source}/{content_hash[:16]}.txt"
    dest = vault / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")

    artifact = Artifact(
        user_id=user.id,
        source=source,
        external_id=external_id or content_hash[:32],
        title=title,
        content_hash=content_hash,
        raw_path=rel,
        metadata_=metadata or {},
    )
    session.add(artifact)
    await session.flush()

    version = settings.chunker_version
    for ord_, chunk_body in chunk_text(body, version=version):
        session.add(
            ArtifactChunk(
                artifact_id=artifact.id,
                ord=ord_,
                text=chunk_body,
                token_count=len(chunk_body.split()),
                chunker_version=version,
            )
        )
    await session.execute(
        text("UPDATE artifacts SET tsv = to_tsvector('english', :body) WHERE id = :id"),
        {"body": body, "id": artifact.id},
    )
    await enqueue(session, "artifact.process", {"artifact_id": str(artifact.id)})
    await session.flush()
    return artifact
