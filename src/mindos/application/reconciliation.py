"""Startup reconciliation (ARCHITECTURE section 11.4): repairs in-flight state before serving.

A checkpointer is not crash recovery: nothing restarts a run whose process died, so the
application must. This module repairs what is only in the database; re-delivering decisions to
paused runs needs the live runtime and lives in `Application.recover` (step 2).

1. Side effects left STARTED were interrupted. Their outcome is unknown, and is recorded as
   UNKNOWN, never as success or failure. Approvals left CONSUMED are settled the same way.
3. Approvals past their TTL are expired.
4. Job-lease recovery arrives with Phase 8; 5. integration state with Phase 6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from opsagent.domain import ApprovalBrokerPort, ApprovalRecord, AuditSink, ExecutionLog

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationReport:
    expired: tuple[ApprovalRecord, ...] = ()
    unknown_executions: tuple[tuple[str, str, str | None], ...] = ()  # (id, tool, run_id)
    unknown_approvals: tuple[ApprovalRecord, ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.expired or self.unknown_executions or self.unknown_approvals)


async def reconcile_storage(
    *, broker: ApprovalBrokerPort, executions: ExecutionLog, audit: AuditSink
) -> ReconciliationReport:
    """Call at startup, before the application serves requests. Every repair is audited."""
    # Step 3 first, so an expired approval is never treated as live.
    expired = tuple(await broker.expire_stale())

    # Step 1: in-flight side effects. Without a provider to ask, the honest state is UNKNOWN.
    unknown = tuple(await executions.abandon_started())
    for execution_id, tool, run_id in unknown:
        await audit.append(
            event_type="tool.outcome_unknown",
            actor="ENGINE",
            subject_type="tool_execution",
            subject_id=execution_id,
            data={"tool": tool, "run_id": run_id, "reason": "process stopped mid-execution"},
        )
    stuck = tuple(await broker.abandon_consumed())
    report = ReconciliationReport(expired, unknown, stuck)

    if report.clean:
        log.info("reconciliation: nothing to repair")
    else:
        log.warning(
            "reconciliation: expired=%d unknown_executions=%d unknown_approvals=%d",
            len(expired),
            len(unknown),
            len(stuck),
        )
    return report
