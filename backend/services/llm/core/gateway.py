from backend.common.utils.enums import LlmMode, LlmTask
from backend.common.utils.exceptions import BudgetExceededError, LlmDisabledError
from backend.common.config_loader.settings import Settings
from backend.services.llm.core.budget import BudgetGuard
from backend.services.llm.providers.google import complete_google
from backend.services.llm.providers.mock import MockProvider
from backend.services.llm.core.types import CompletionResult

_MAX_INPUT_CHARS = 32_000
_MAX_OUTPUT_TOKENS = 2_048


class ProviderGateway:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._mode = settings.mindos_llm_mode
        self._budget = BudgetGuard(
            daily_usd_cap=settings.mindos_llm_daily_usd,
            monthly_usd_cap=settings.mindos_llm_monthly_usd,
            kill_switch=settings.mindos_llm_kill_switch,
        )
        self._mock = MockProvider()

    @property
    def mode(self) -> LlmMode:
        return self._mode

    def model_for(self, task: LlmTask, override: str | None = None) -> str:
        if override:
            return override
        if task == LlmTask.EXTRACT:
            return self._settings.mindos_extract_model
        return self._settings.mindos_synth_model

    async def complete(
        self,
        prompt: str,
        *,
        task: LlmTask = LlmTask.SYNTH,
        model: str | None = None,
        max_output_tokens: int = 1024,
    ) -> CompletionResult:
        prompt = prompt[:_MAX_INPUT_CHARS]
        max_output_tokens = min(max_output_tokens, _MAX_OUTPUT_TOKENS)
        resolved_model = self.model_for(task, model)

        if self._mode == LlmMode.MOCK:
            return await self._mock.complete(
                prompt, model=resolved_model, max_output_tokens=max_output_tokens
            )
        if self._mode == LlmMode.DEGRADED:
            raise LlmDisabledError("LLM calls disabled in degraded mode")
        if not self._budget.allow_request():
            raise BudgetExceededError("Daily or monthly LLM budget exceeded")

        provider = self._settings.mindos_default_provider.lower()
        if provider == "google":
            return await complete_google(
                prompt,
                model=resolved_model,
                api_key=self._settings.google_api_key,
                max_output_tokens=max_output_tokens,
            )
        raise NotImplementedError(f"Live provider {provider!r} is not configured")


def build_llm_gateway(settings: Settings) -> ProviderGateway:
    return ProviderGateway(settings)
