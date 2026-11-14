from backend.services.llm.core.types import CompletionResult


class MockProvider:
    async def complete(self, prompt: str, *, model: str, max_output_tokens: int) -> CompletionResult:
        return CompletionResult(
            text="[mock] " + prompt[:200],
            provider="mock",
            model=model,
            input_tokens=min(len(prompt) // 4, 8000),
            output_tokens=32,
        )
