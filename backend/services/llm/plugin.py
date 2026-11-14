from __future__ import annotations

from typing import ClassVar

from backend.plugin import ServicePlugin


class LlmPlugin(ServicePlugin):
    name: ClassVar[str] = "llm"


plugin = LlmPlugin()
