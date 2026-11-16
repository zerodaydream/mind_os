import asyncio
from collections.abc import AsyncIterator, Iterator

from backend.common.ssl_env import fix_ssl_cert_env
from backend.services.llm.core.types import CompletionResult

StreamPiece = tuple[str, str]  # (kind, text) kind ∈ think | answer


def gemini_model_major(model: str) -> int:
    """Gemini API major version from model id (e.g. gemini-3.5-flash-lite → 3)."""
    mid = (model or "").lower().removeprefix("models/")
    if not mid.startswith("gemini-"):
        return 2
    head = mid.removeprefix("gemini-").split("-", 1)[0]
    major = head.split(".", 1)[0]
    return int(major) if major.isdigit() else 2


def build_gemini_thinking_config(
    model: str,
    level: str,
    *,
    budget: int = 1024,
) -> dict | None:
    """Thinking + streamed thoughts; None when level is OFF."""
    level = (level or "").strip().upper()
    if level in {"", "0", "OFF", "NONE", "DISABLED"}:
        return None
    cfg: dict = {"include_thoughts": True}
    if gemini_model_major(model) >= 3:
        cfg["thinking_level"] = level
    else:
        cfg["thinking_budget"] = budget
    return cfg


def google_generate_config(
    *,
    max_output_tokens: int,
    thinking_config: dict | None = None,
) -> dict:
    cfg: dict = {"max_output_tokens": max_output_tokens}
    if thinking_config:
        cfg["thinking_config"] = thinking_config
    return cfg


def _parts_from_chunk(chunk) -> Iterator[StreamPiece]:
    saw = False
    for cand in getattr(chunk, "candidates", None) or []:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            text = (getattr(part, "text", None) or "").strip()
            if not text:
                continue
            saw = True
            kind = "think" if getattr(part, "thought", None) else "answer"
            yield kind, text
    if not saw:
        text = (getattr(chunk, "text", None) or "").strip()
        if text:
            yield "answer", text


async def stream_google(
    prompt: str,
    *,
    model: str,
    api_key: str,
    max_output_tokens: int,
    thinking_config: dict | None = None,
) -> AsyncIterator[StreamPiece]:
    if not api_key.strip():
        raise ValueError("GOOGLE_API_KEY is required for live Google provider")

    from google import genai

    fix_ssl_cert_env()
    client = genai.Client(api_key=api_key)
    try:
        stream = await client.aio.models.generate_content_stream(
            model=model,
            contents=prompt,
            config=google_generate_config(
                max_output_tokens=max_output_tokens,
                thinking_config=thinking_config,
            ),
        )
        async for chunk in stream:
            for piece in _parts_from_chunk(chunk):
                yield piece
    except Exception as exc:
        raise ValueError(f"Google generate_content_stream failed for model={model!r}: {exc}") from exc
    finally:
        try:
            await client.aio.aclose()
        except Exception:
            pass


async def complete_google(
    prompt: str,
    *,
    model: str,
    api_key: str,
    max_output_tokens: int,
    thinking_config: dict | None = None,
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
                config=google_generate_config(
                    max_output_tokens=max_output_tokens,
                    thinking_config=thinking_config,
                ),
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