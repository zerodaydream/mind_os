"""Plan mode is enforced by the engine, not merely displayed (ARCHITECTURE 13.2 ModeChanged)."""

from __future__ import annotations

from opsagent.application import Application, EventBus
from opsagent.domain import ApprovalMode, SessionMode
from opsagent.domain.commands import SetMode
from opsagent.infra import SystemClock, new_id
from tests.support.approval_kit import Env


async def test_the_mode_the_ui_sets_is_the_mode_the_policy_enforces() -> None:
    mode = SessionMode()
    env = Env(mode=mode)
    app = Application(runtime=object(), bus=EventBus(SystemClock()), new_id=new_id, mode=mode)  # type: ignore[arg-type]

    assert (await env.propose()).status == "pending_approval"  # default mode: ask the user
    app.set_mode(SetMode(mode=ApprovalMode.PLAN))
    refused = await env.propose(call_id="call_2")
    assert refused.status == "denied" and refused.error_code == "policy_denied"
    assert (
        await env.propose("mail.search", {"q": "x"}, call_id="call_3")
    ).status == "ok"  # reads ok
    app.set_mode(SetMode(mode=ApprovalMode.DEFAULT))
    assert (await env.propose(call_id="call_4")).status == "pending_approval"
