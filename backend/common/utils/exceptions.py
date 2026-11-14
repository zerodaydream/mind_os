class MindOSError(Exception):
    """Base domain error."""


class BudgetExceededError(MindOSError):
    """LLM daily or monthly cap hit."""


class LlmDisabledError(MindOSError):
    """MINDOS_LLM_MODE blocks provider calls."""

