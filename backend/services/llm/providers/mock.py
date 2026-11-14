from collections.abc import AsyncIterator

from backend.services.llm.core.types import CompletionResult


class MockProvider:
    async def complete(self, prompt: str, *, model: str, max_output_tokens: int) -> CompletionResult:
        text = "[mock] " + prompt[:200]
        return CompletionResult(
            text=text,
            provider="mock",
            model=model,
            input_tokens=min(len(prompt) // 4, 8000),
            output_tokens=max(1, len(text) // 4),
        )

    async def stream(
        self, prompt: str, *, model: str, max_output_tokens: int
    ) -> AsyncIterator[str]:
        text = "[mock] " + prompt[:200]
        step = 12
        for i in range(0, len(text), step):
            yield text[i : i + step]