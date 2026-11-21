"""A chat model that plays a fixed script of replies, with streaming and tool calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, PrivateAttr


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def reply(
    text: str = "",
    *,
    calls: Sequence[dict[str, Any]] = (),
    thinking: str | None = None,
    tokens: tuple[int, int, int] = (10, 5, 0),
) -> AIMessage:
    content: Any = text
    if thinking:
        content = [{"type": "thinking", "thinking": thinking}, {"type": "text", "text": text}]
    inp, out, reasoning = tokens
    return AIMessage(
        content=content,
        tool_calls=list(calls),
        usage_metadata={
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "output_token_details": {"reasoning": reasoning},
        },
    )


class ScriptedChatModel(BaseChatModel):
    """Replays `script` in order; repeats `fallback` after it runs out.

    Records the tool names bound to it (`bound_tool_names`) and every prompt it saw (`seen`).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    script: list[AIMessage]
    fallback: AIMessage | None = None
    on_call: Callable[[], None] | None = None
    delay: float = 0.0
    _index: int = PrivateAttr(default=0)
    _bound: list[str] = PrivateAttr(default_factory=list)
    _seen: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def bound_tool_names(self) -> list[str]:
        return self._bound

    @property
    def seen(self) -> list[list[BaseMessage]]:
        return self._seen

    def load(self, *script: AIMessage, fallback: AIMessage | None = None) -> None:
        """Replace the script and start over."""
        self.script, self.fallback, self._index = list(script), fallback, 0

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        self._bound = [getattr(t, "name", None) or t["function"]["name"] for t in tools]
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self._seen.append(list(messages))
        if self.on_call:
            self.on_call()
        if self._index < len(self.script):
            message = self.script[self._index]
            self._index += 1
            return message
        if self.fallback is not None:
            return self.fallback
        raise AssertionError("script exhausted")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from self._chunks(self._next(messages))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        message = self._next(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        for chunk in self._chunks(message):
            yield chunk

    @staticmethod
    def _chunks(message: AIMessage) -> Iterator[ChatGenerationChunk]:
        blocks = message.content if isinstance(message.content, list) else [message.content]
        for block in blocks:
            if isinstance(block, dict):
                piece: Any = [block]
            elif block:
                piece = block
            else:
                continue
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece, id=message.id))
        for index, call in enumerate(message.tool_calls):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    id=message.id,
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": index,
                        }
                    ],
                )
            )
        yield ChatGenerationChunk(
            message=AIMessageChunk(content="", id=message.id, usage_metadata=message.usage_metadata)
        )
