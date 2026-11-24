"""Which model and reasoning effort each role uses, changeable while the app runs.

The tuner owns the current effort per role; the provider owns the current model per role. The
runtime and the subagent runner read both at the moment they build a graph, and cache graphs by
(model, effort), so a change applies from the next model call and never mutates a running graph.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mindos.domain import ModelChoice, ModelRole, ReasoningEffort, resolve_effort


class ModelCatalog(Protocol):
    """What a provider can say about its models. Implemented by the Gemini adapter."""

    async def list_models(self) -> tuple[str, ...]: ...

    def effort_levels(self, model_id: str) -> tuple[ReasoningEffort, ...]: ...

    def select_model(self, role: ModelRole, model_id: str) -> None: ...


class ModelTuner:
    def __init__(
        self,
        *,
        efforts: dict[ModelRole, ReasoningEffort],
        model_id: Callable[[ModelRole], str],
        catalog: ModelCatalog | None = None,
        extra_models: tuple[str, ...] = (),
    ) -> None:
        self._efforts = dict(efforts)
        self._model_id = model_id
        self._catalog = catalog
        self._extra = extra_models
        self._known: tuple[str, ...] = ()

    def effort(self, role: ModelRole) -> ReasoningEffort | None:
        return self._efforts.get(role)

    # ---- ModelTuning ------------------------------------------------------------------------
    def current(self, role: ModelRole) -> ModelChoice:
        return ModelChoice(role=role, model_id=self._model_id(role), effort=self._efforts.get(role))

    async def available_models(self) -> tuple[str, ...]:
        listed = await self._catalog.list_models() if self._catalog else ()
        configured = tuple(
            self.current(r).model_id for r in (ModelRole.ORCHESTRATOR, ModelRole.SUBAGENT)
        )
        self._known = tuple(dict.fromkeys([*configured, *self._extra, *listed]))
        return self._known

    def effort_levels(self, model_id: str) -> tuple[ReasoningEffort, ...]:
        if self._catalog is None:
            return tuple(ReasoningEffort)
        try:
            return self._catalog.effort_levels(model_id)
        except Exception:  # unknown model or no key yet: offer nothing rather than guess
            return ()

    def select(
        self,
        role: ModelRole,
        *,
        model_id: str | None = None,
        effort: ReasoningEffort | None = None,
    ) -> ModelChoice:
        if model_id is not None and model_id != self._model_id(role):
            if self._known and model_id not in self._known:
                raise ValueError(f"'{model_id}' is not one of the available models.")
            if self._catalog is None:
                raise ValueError("Models cannot be changed in this session.")
            self._catalog.select_model(role, model_id)
        final_model = model_id or self._model_id(role)
        levels = self.effort_levels(final_model)
        wanted = effort or self._efforts.get(role)
        if effort is not None and levels and effort not in levels:
            allowed = ", ".join(e.value for e in levels)
            raise ValueError(f"{final_model} supports: {allowed}.")
        if effort is not None and not levels:
            raise ValueError(f"{final_model} has no configurable reasoning.")
        if wanted is not None and levels:  # a model change may leave the old level unsupported
            resolved = resolve_effort(wanted, levels)
            wanted = resolved.effort if resolved else None
        if wanted is not None:
            self._efforts[role] = wanted
        return self.current(role)
