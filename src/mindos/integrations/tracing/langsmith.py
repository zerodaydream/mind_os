"""LangSmith tracing behind the `Tracing` port (ARCHITECTURE section 15, ADR 0007).

LangChain, LangGraph, and Deep Agents trace themselves once a tracing context is active, so this
adapter only decides *whether* and *where* and puts every run in one trace with a thread ID:

- Tracing is switched by our settings, never by the process environment. When it is off the
  adapter still opens `tracing_context(enabled=False)`, so a stray `LANGSMITH_TRACING=true` in the
  shell cannot send an operator's mail and calendar to a third party.
- Everything sent passes through `redact` (secrets masked), or nothing is sent but structure.
- Runs go to an agent address when an agent ID is set (agent-based workspaces), else a project.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from langsmith import Client, tracing_context

from opsagent.domain import redact

log = logging.getLogger(__name__)

_AGENT_ID = re.compile(r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool = False
    api_key: str | None = None
    project: str = "opsagent"
    agent_id: str | None = None
    environment: str = "local"
    endpoint: str | None = None
    workspace_id: str | None = None
    hide_io: bool = False
    sampling_rate: float = 1.0
    version: str = ""


def _scrub(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = redact(dict(payload))
    return result if isinstance(result, dict) else {}


def _nothing(_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {}


class LangSmithTracing:
    def __init__(self, config: TracingConfig, client: Client | None = None) -> None:
        self._cfg = config
        self._client = client
        if config.agent_id is not None and not _AGENT_ID.match(config.agent_id):
            raise ValueError("langsmith agent ID must be 1-63 lowercase letters, digits, hyphens")
        if config.enabled and client is None:
            self._client = self._make_client(config)

    @property
    def active(self) -> bool:
        return self._cfg.enabled and self._client is not None

    def run(
        self, *, run_id: str, thread_id: str, conversation_id: str | None
    ) -> AbstractContextManager[None]:
        """One agent run. `thread_id` groups a conversation's runs into a LangSmith thread."""
        if not self.active:
            return tracing_context(enabled=False)  # beats any LANGSMITH_TRACING in the shell
        cfg = self._cfg
        metadata = {
            "thread_id": thread_id,
            "opsagent_run_id": run_id,
            "opsagent_version": cfg.version,
            **({"conversation_id": conversation_id} if conversation_id else {}),
        }
        destination: dict[str, Any] = (
            {
                "address": {
                    "kind": "AGENT",
                    "id": cfg.agent_id,
                    "environment": cfg.environment.upper(),
                }
            }
            if cfg.agent_id
            else {"project_name": cfg.project}
        )
        return tracing_context(
            enabled=True,
            client=self._client,
            metadata=metadata,
            tags=["opsagent", cfg.environment],
            **destination,
        )

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:
                log.warning("could not flush LangSmith traces", exc_info=True)

    @staticmethod
    def _make_client(cfg: TracingConfig) -> Client | None:
        if not cfg.api_key:
            log.warning("LangSmith tracing is on but no API key is set; tracing is off")
            return None
        hide = _nothing if cfg.hide_io else _scrub
        return Client(
            api_key=cfg.api_key,
            api_url=cfg.endpoint,
            workspace_id=cfg.workspace_id,
            hide_inputs=hide,
            hide_outputs=hide,
            tracing_sampling_rate=cfg.sampling_rate,
        )
