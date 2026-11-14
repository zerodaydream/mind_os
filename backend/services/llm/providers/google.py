import asyncio

from backend.common.ssl_env import fix_ssl_cert_env
from backend.services.llm.core.types import CompletionResult


async def complete_google(
    prompt: str,
    *,
    model: str,
    api_key: str,
    max_output_tokens: int,
) -> CompletionResult:
    if not api_key.strip():
        raise ValueError("GOOGLE_API_KEY is required for live Google provider")

    def _call() -> CompletionResult:
        from google import genai

        fix_ssl_cert_env()
        client = genai.Client(api_key=api_key)
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"max_output_tokens": max_output_tokens},
            )
        except Exception as exc:
            raise ValueError(f"Google generate_content failed for model={model!r}: {exc}") from exc
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise ValueError(f"Google returned empty content for model={model!r}")
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) or len(prompt) // 4
        output_tokens = getattr(usage, "candidates_token_count", None) or len(text) // 4
        return CompletionResult(
            text=text,
            provider="google",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return await asyncio.to_thread(_call)
