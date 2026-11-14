from enum import StrEnum


class LlmMode(StrEnum):
    LIVE = "live"
    DEGRADED = "degraded"
    MOCK = "mock"


class LlmTask(StrEnum):
    EXTRACT = "extract"
    SYNTH = "synth"
