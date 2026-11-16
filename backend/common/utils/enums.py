from enum import StrEnum


class LlmMode(StrEnum):
    """LLM mode: live, degraded, mock."""
    LIVE = "live"
    DEGRADED = "degraded"
    MOCK = "mock"


class LlmTask(StrEnum):
    """LLM task: extract, synth."""
    EXTRACT = "extract"
    SYNTH = "synth"
