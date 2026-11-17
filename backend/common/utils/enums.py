from enum import StrEnum


class LlmMode(StrEnum):
    """LLM mode: live, degraded, mock."""
    LIVE = "live"
    DEGRADED = "degraded"
    MOCK = "mock"


class LlmTask(StrEnum):
    """LLM task: router (cheap prep), extract, synth."""
    ROUTER = "router"
    EXTRACT = "extract"
    SYNTH = "synth"
