"""Typed settings. Model IDs are configuration with no defaults in code (README: Configuration)."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opsagent.domain import ModelRole, ReasoningEffort


class ConfigError(RuntimeError):
    """Settings are missing or inconsistent. The message says what to set."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPS_", extra="ignore", populate_by_name=True)

    # Database
    database_url: str = "postgresql://opsagent:opsagent@localhost:5433/opsagent"
    db_pool_max: int = Field(default=10, ge=1, le=100)

    # Models
    model_orchestrator: str | None = None
    model_subagent: str | None = None
    model_utility: str | None = None
    model_embedding: str | None = None
    embedding_dimensions: int | None = Field(default=None, gt=0)

    # Reasoning
    reasoning_orchestrator: ReasoningEffort = ReasoningEffort.HIGH
    reasoning_subagent: ReasoningEffort = ReasoningEffort.LOW
    reasoning_utility: ReasoningEffort = ReasoningEffort.MINIMAL
    include_thoughts: bool = True
    thinking_budget: int | None = Field(default=None, ge=-1)  # Gemini 2.5 only

    # Dev-only credential fallback. Production reads the macOS Keychain (Phase 5).
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )

    # Agent limits
    max_parallel_subagents: int = Field(default=3, ge=1, le=16)
    run_deadline_seconds: int = Field(default=900, gt=0)

    # Observability
    log_level: str = "INFO"
    log_dir: Path = Path("~/Library/Logs/opsagent")

    @field_validator("log_dir")
    @classmethod
    def _expand_log_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level {value!r}")
        return level

    def model_for(self, role: ModelRole) -> str:
        """Model ID for a role. The utility role falls back to the subagent model."""
        candidates = {
            ModelRole.ORCHESTRATOR: (self.model_orchestrator,),
            ModelRole.SUBAGENT: (self.model_subagent,),
            ModelRole.UTILITY: (self.model_utility, self.model_subagent),
        }[role]
        for value in candidates:
            if value:
                return value
        raise ConfigError(
            f"No model configured for role '{role.value}'. Set OPS_MODEL_{role.name}."
        )

    def models(self) -> dict[ModelRole, str]:
        """Configured models by role. Roles without a model are omitted."""
        out: dict[ModelRole, str] = {}
        for role in ModelRole:
            try:
                out[role] = self.model_for(role)
            except ConfigError:
                continue
        return out

    def reasoning_for(self, role: ModelRole) -> ReasoningEffort:
        return {
            ModelRole.ORCHESTRATOR: self.reasoning_orchestrator,
            ModelRole.SUBAGENT: self.reasoning_subagent,
            ModelRole.UTILITY: self.reasoning_utility,
        }[role]


def load_settings(env_file: Path | str | None = ".env") -> Settings:
    """Load settings from the process environment plus an optional dotenv file.

    The dotenv file is parsed by this function only; it is never exported into the process
    environment, so child processes do not inherit secrets.
    """
    path = Path(env_file) if env_file else None
    if path is not None and not path.is_file():
        path = None
    return Settings(_env_file=path)
