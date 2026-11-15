from collections.abc import AsyncIterator

from backend.common.langsmith_setup import tracing_active, turn_trace_id, unique_run_name
from backend.common.utils.enums import LlmMode, LlmTask
from backend.common.utils.exceptions import BudgetExceededError, LlmDisabledError
from backend.common.config_loader.settings import Settings
from backend.services.llm.core.budget import BudgetGuard
from backend.services.llm.providers.google import stream_google
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

    def _prepare(self, prompt: str, *, task: LlmTask, model: str | None, max_output_tokens: int):
        prompt = prompt[:_MAX_INPUT_CHARS]
        max_output_tokens = min(max_output_tokens, _MAX_OUTPUT_TOKENS)
        resolved_model = self.model_for(task, model)
        return prompt, resolved_model, max_output_tokens

    async def _iter_live_chunks(
        self, prompt: str, resolved_model: str, max_output_tokens: int
    ) -> AsyncIterator[str]:
        provider = self._settings.mindos_default_provider.lower()
        if provider == "google":
            async for part in stream_google(
                prompt,
                model=resolved_model,
                api_key=self._settings.google_api_key,
                max_output_tokens=max_output_tokens,
            ):
                yield part
            return
        raise NotImplementedError(f"Live provider {provider!r} is not configured")

    async def stream(
        self,
        prompt: str,
        *,
        task: LlmTask = LlmTask.SYNTH,
        model: str | None = None,
        max_output_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        prompt, resolved_model, max_output_tokens = self._prepare(
            prompt, task=task, model=model, max_output_tokens=max_output_tokens
        )
        if self._mode == LlmMode.MOCK:
            async for part in self._mock.stream(
                prompt, model=resolved_model, max_output_tokens=max_output_tokens
            ):
                yield part
            return
        if self._mode == LlmMode.DEGRADED:
            raise LlmDisabledError("LLM calls disabled in degraded mode")
        if not self._budget.allow_request():
            raise BudgetExceededError("Daily or monthly LLM budget exceeded")

        parts: list[str] = []
        ls_cm = None
        ls_run = None
        if tracing_active():
            from langsmith.run_helpers import trace

            ls_cm = trace(
                name=unique_run_name("mindos_llm_stream"),
                run_type="llm",
                inputs={
                    "prompt": prompt[:4000],
                    "model": resolved_model,
                    "task": task.value,
                    "max_output_tokens": max_output_tokens,
                    "turn_trace_id": turn_trace_id(),
                },
            )
            ls_run = ls_cm.__enter__()
        try:
            async for part in self._iter_live_chunks(prompt, resolved_model, max_output_tokens):
                parts.append(part)
                yield part
        finally:
            if ls_run is not None:
                text = "".join(parts)
                ls_run.end(outputs={"text": text[:8000], "chars": len(text)})
            if ls_cm is not None:
                ls_cm.__exit__(None, None, None)

    async def complete(
        self,
        prompt: str,
        *,
        task: LlmTask = LlmTask.SYNTH,
        model: str | None = None,
        max_output_tokens: int = 1024,
    ) -> CompletionResult:
        prompt, resolved_model, max_output_tokens = self._prepare(
            prompt, task=task, model=model, max_output_tokens=max_output_tokens
        )
        parts: list[str] = []
        async for chunk in self.stream(
            prompt, task=task, model=resolved_model, max_output_tokens=max_output_tokens
        ):
            parts.append(chunk)
        text = "".join(parts).strip()
        if self._mode == LlmMode.MOCK:
            provider = "mock"
        elif self._mode == LlmMode.DEGRADED:
            raise LlmDisabledError("LLM calls disabled in degraded mode")
        else:
            provider = self._settings.mindos_default_provider.lower()
        return CompletionResult(
            text=text,
            provider=provider,
            model=resolved_model,
            input_tokens=min(len(prompt) // 4, 8000),
            output_tokens=max(1, len(text) // 4),
        )


def build_llm_gateway(settings: Settings) -> ProviderGateway:
    return ProviderGateway(settings)
