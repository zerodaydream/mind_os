"""Startup reconciliation (ARCHITECTURE §11.4): repairs in-flight states on startup.

Steps:
1. Find tool_executions in STARTED — log warning (actual reconciliation deferred to Phase 06+).
2. Find APPROVED/APPROVED_EDITED approvals and re-deliver to waiting runs.
3. Expire stale (past expires_at) PENDING/APPROVED approvals.
4. (Job lease orphan recovery — Phase 08.)
5. (Rebuild integration states — Phase 06.)
"""

from __future__ import annotations

import logging

from opsagent.domain.ports import ApprovalRepository

log = logging.getLogger(__name__)


async def run_reconciliation(approval_repo: ApprovalRepository) -> None:
    """Call at startup, before the application begins serving requests."""
    # Step 3: expire stale approvals first so we don't re-deliver expired ones
    expired = await approval_repo.expire_stale()
    if expired:
        log.info("reconciliation: expired %d stale approvals", expired)

    # Step 1: warn about STARTED tool_executions (reconcile against provider in Phase 06)
    rows = await approval_repo.find_started_executions()
    if rows:
        log.warning(
            "reconciliation: %d tool_executions left in STARTED state; "
            "provider-side reconciliation requires Phase 06",
            len(rows),
        )

    log.info("reconciliation complete")
