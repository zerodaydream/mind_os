from collections.abc import AsyncIterator, Callable

from backend.common.langsmith_setup import tracing_active, turn_trace_id, unique_run_name
from backend.common.utils.enums import LlmMode, LlmTask
from backend.common.utils.exceptions import BudgetExceededError, LlmDisabledError
from backend.common.config_loader.settings import Settings
from backend.services.agents.core.render.chat_stream import emit_chat_stream, merge_chat_stream_text
from backend.services.llm.core.budget import BudgetGuard
from backend.services.llm.providers.google import stream_google
from backend.services.llm.providers.mock import MockProvider
from backend.services.llm.core.types import CompletionResult

_MAX_INPUT_CHARS = 32_000
# Output budget must sit above typical thinking budgets so answers are not cut mid-sentence.
_MAX_OUTPUT_TOKENS = 4_096


def _clamp_prompt_keep_tail(prompt: str, max_chars: int) -> str:
    """Drop the head of an oversize prompt so the task/question at the end survives."""
    if len(prompt) <= max_chars:
        return prompt
    marker = "…\n"
    keep = max_chars - len(marker)
    if keep < 1:
        return prompt[-max_chars:]
    return marker + prompt[-keep:]


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

    @property
    def budget(self) -> BudgetGuard:
        return self._budget

    def model_for(self, task: LlmTask, override: str | None = None) -> str:
        if override:
            return override
        if task == LlmTask.ROUTER:
            return self._settings.mindos_router_model
        if task == LlmTask.EXTRACT:
            return self._settings.mindos_extract_model
        return self._settings.mindos_synth_model

    def thinking_config_for(self, task: LlmTask, model: str) -> dict | None:
        # User-visible chat (router/synth) must not stream Gemini thought blocks to the TUI.
        del model  # deep agent sets its own thinking_config on the graph path.
        return None

    def _prepare(self, prompt: str, *, task: LlmTask, model: str | None, max_output_tokens: int):
        prompt = _clamp_prompt_keep_tail(prompt, _MAX_INPUT_CHARS)
        max_output_tokens = min(max_output_tokens, _MAX_OUTPUT_TOKENS)
        resolved_model = self.model_for(task, model)
        return prompt, resolved_model, max_output_tokens

    def _record_completion(self, prompt: str, text: str) -> None:
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(text) // 4) if text else 0
        self._budget.record_usage(input_tokens, output_tokens)

    async def _iter_live_chunks(
        self,
        prompt: str,
        resolved_model: str,
        max_output_tokens: int,
        *,
        task: LlmTask,
    ) -> AsyncIterator[tuple[str, str]]:
        provider = self._settings.mindos_default_provider.lower()
        if provider == "google":
            async for part in stream_google(
                prompt,
                model=resolved_model,
                api_key=self._settings.google_api_key,
                max_output_tokens=max_output_tokens,
                thinking_config=self.thinking_config_for(task, resolved_model),
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
        on_partial: Callable[..., None] | None = None,
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

        think_text = ""
        answer: list[str] = []
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
            async for kind, piece in self._iter_live_chunks(
                prompt, resolved_model, max_output_tokens, task=task
            ):
                if kind == "think":
                    think_text = merge_chat_stream_text(think_text, piece)
                    emit_chat_stream(on_partial, think_text, phase="think")
                else:
                    answer.append(piece)
                    emit_chat_stream(on_partial, "".join(answer), phase="answer")
                    yield piece
        finally:
            text = "".join(answer)
            if self._mode == LlmMode.LIVE:
                self._record_completion(prompt, text)
            if ls_run is not None:
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
        on_partial: Callable[..., None] | None = None,
    ) -> CompletionResult:
        prompt, resolved_model, max_output_tokens = self._prepare(
            prompt, task=task, model=model, max_output_tokens=max_output_tokens
        )
        parts: list[str] = []
        async for chunk in self.stream(
            prompt,
            task=task,
            model=resolved_model,
            max_output_tokens=max_output_tokens,
            on_partial=on_partial,
        ):
            parts.append(chunk)
        text = "".join(parts).strip()
        if self._mode == LlmMode.MOCK:
            provider = "mock"
            # Mock path skips live stream accounting; still record for budget tests.
            self._record_completion(prompt, text)
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


def build_provider_gateway(settings: Settings) -> ProviderGateway:
    return ProviderGateway(settings)