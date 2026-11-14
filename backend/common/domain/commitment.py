from datetime import datetime

from pydantic import BaseModel, Field


class CommitmentDraft(BaseModel):
    summary: str
    actor: str | None = None
    counterparty: str | None = None
    due_at: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    extraction_mode: str = "rules"
