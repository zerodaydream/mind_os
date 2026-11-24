"""A database's worth of approval state that outlives any one runtime (a "restart")."""

from __future__ import annotations

from typing import Any

from opsagent.application import ApprovalBroker
from opsagent.domain import PolicyDecision
from opsagent.infra import new_id
from opsagent.tools.policy import PolicyConfig, PolicyEngine, ToolPolicy
from tests.support.agent_kit import Kit, make_kit
from tests.support.fakes import ManualClock, MemoryApprovalRepo, MemoryAudit, MemoryExecutionLog
from tests.support.scripted_model import ScriptedChatModel

GATED = PolicyConfig(overrides=[ToolPolicy("write_todos", PolicyDecision.REQUIRE_APPROVAL)])


class World:
    """A database's worth of approval state that outlives any one runtime (a "restart")."""

    def __init__(self) -> None:
        self.clock = ManualClock()
        self.repo = MemoryApprovalRepo(self.clock)
        self.audit = MemoryAudit()
        self.executions = MemoryExecutionLog()
        self.saver: Any = None

    def broker(self) -> ApprovalBroker:
        return ApprovalBroker(repo=self.repo, audit=self.audit, new_id=new_id, clock=self.clock)

    def kit(
        self, *script: Any, subagent: ScriptedChatModel | None = None, config: Any = None
    ) -> Kit:
        kit = make_kit(
            ScriptedChatModel(script=list(script)),
            subagent,
            policy=PolicyEngine(config or GATED),
            broker=self.broker(),
            audit=self.audit,
            saver=self.saver,
            executions=self.executions,
        )
        self.saver = kit.saver
        return kit
