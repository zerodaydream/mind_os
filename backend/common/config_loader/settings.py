from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.common.utils.enums import LlmMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+asyncpg://mindos:mindos@localhost:5433/mindos",
        validation_alias="DATABASE_URL",
    )
    mindos_secret_key: str = "change-me"
    mindos_llm_mode: LlmMode = Field(default=LlmMode.DEGRADED, validation_alias="MINDOS_LLM_MODE")
    mindos_llm_daily_usd: float = Field(default=2.0, validation_alias="MINDOS_LLM_DAILY_USD")
    mindos_llm_monthly_usd: float = Field(default=40.0, validation_alias="MINDOS_LLM_MONTHLY_USD")
    mindos_llm_kill_switch: bool = Field(default=False, validation_alias="MINDOS_LLM_KILL_SWITCH")
    worker_poll_sec: float = Field(default=2.0, validation_alias="MINDOS_WORKER_POLL_SEC")
    mindos_default_provider: str = Field(
        default="google", validation_alias="MINDOS_DEFAULT_PROVIDER"
    )
    mindos_extract_model: str = Field(
        default="gemini-3.5-flash-lite", validation_alias="MINDOS_EXTRACT_MODEL"
    )
    mindos_synth_model: str = Field(
        default="gemini-3.5-flash-lite", validation_alias="MINDOS_SYNTH_MODEL"
    )
    mindos_thinking_level: str = Field(
        default="MEDIUM",
        validation_alias="MINDOS_THINKING_LEVEL",
    )
    mindos_thinking_budget: int = Field(
        default=1024,
        validation_alias="MINDOS_THINKING_BUDGET",
    )
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")
    mindos_default_user_email: str = Field(
        default="local@mindos", validation_alias="MINDOS_DEFAULT_USER_EMAIL"
    )
    chunker_version: str = Field(default="v2", validation_alias="CHUNKER_VERSION")
    embedding_model: str = Field(
        default="BAAI/bge-base-en-v1.5",
        validation_alias="EMBEDDING_MODEL",
    )
    embedding_dimensions: int = Field(default=768, validation_alias="EMBEDDING_DIMENSIONS")
    google_oauth_client_id: str = Field(default="", validation_alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = Field(default="", validation_alias="GOOGLE_OAUTH_CLIENT_SECRET")
    google_oauth_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/v1/integrations/google/callback",
        validation_alias="GOOGLE_OAUTH_REDIRECT_URI",
    )
    mindos_ui_origin: str = Field(
        default="",
        validation_alias="MINDOS_UI_ORIGIN",
    )
    gmail_sync_interval_sec: float = Field(default=0.0, validation_alias="GMAIL_SYNC_INTERVAL_SEC")
    mindos_sync_interval_days: float = Field(default=2.0, validation_alias="MINDOS_SYNC_INTERVAL_DAYS")
    gmail_sync_query: str = Field(default="", validation_alias="GMAIL_SYNC_QUERY")
    gmail_sync_max_messages: int = Field(default=200, validation_alias="GMAIL_SYNC_MAX_MESSAGES")
    mindos_default_timezone: str = Field(
        default="",
        validation_alias="MINDOS_DEFAULT_TIMEZONE",
    )
    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_api_key: str = Field(default="", validation_alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="", validation_alias="LANGSMITH_PROJECT")
    langsmith_agent_id: str = Field(default="", validation_alias="LANGSMITH_AGENT_ID")
    langsmith_agent_environment: str = Field(
        default="", validation_alias="LANGSMITH_AGENT_ENVIRONMENT"
    )
    langsmith_endpoint: str = Field(default="", validation_alias="LANGSMITH_ENDPOINT")

    @model_validator(mode="after")
    def _reject_weak_secret_in_live_mode(self) -> "Settings":
        if self.mindos_llm_mode == LlmMode.LIVE and self.mindos_secret_key.strip() in {
            "",
            "change-me",
        }:
            raise ValueError("Set MINDOS_SECRET_KEY before MINDOS_LLM_MODE=live")
        return self


@lru_cache
def get_settings() -> Settings:
    from backend.common.langsmith_setup import configure_langsmith

    settings = Settings()
    configure_langsmith(settings)
    return settings


def reload_settings() -> Settings:
    from backend.common.langsmith_setup import reset_langsmith_config

    get_settings.cache_clear()
    reset_langsmith_config()
    return get_settings()
