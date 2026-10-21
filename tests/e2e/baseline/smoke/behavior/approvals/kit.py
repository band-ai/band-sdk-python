from __future__ import annotations

import logging
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import band.adapters.opencode
from band.core.simple_adapter import SimpleAdapter
from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import Adapter
from tests.e2e.baseline.requires import require_dep
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvalroom import ApprovalRoom
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    Outcome,
)
from tests.e2e.baseline.timeouts import SlowTurnBudget, slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.logs import sdk_logs_at
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


def readback_commands(target: Path) -> frozenset[str]:
    """The one optional read-only follow-up seen from coding agents after a write."""
    return frozenset(
        {
            f"cat {target.name}",
            f'cat "{target.name}"',
            f'Get-Content "{target.name}"',
            f'Get-Content -LiteralPath "{target}"',
        }
    )


TURN_BUDGET_S = BaselineSettings().e2e_timeout

BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=2)

THREE_BARRIERS = slow_turn_budget(TURN_BUDGET_S, barriers=3)

QUEUED_WORK_BUDGET = slow_turn_budget(TURN_BUDGET_S, barriers=6)

EXPIRING_WAIT_S = 10.0

# Keep approval decisions below Cursor's default turn timeout.
PATIENT_WAIT_S = THREE_BARRIERS.deadline_s * 2

REFUSING = tuple(a for a, dialect in DIALECTS.items() if dialect.refusal)

REMEMBERING = tuple(a for a, dialect in DIALECTS.items() if dialect.session_approval)

ASKING = tuple(a for a, dialect in DIALECTS.items() if dialect.question)


@asynccontextmanager
async def approval_room(
    cell: AdapterCell,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    *,
    label: str,
    budget: SlowTurnBudget,
    wait_timeout_s: float = PATIENT_WAIT_S,
    approvers: frozenset[str] | None = None,
    build: Callable[[BaselineSettings, AgentSetup], SimpleAdapter[Any]] | None = None,
    session_config: SessionConfig | None = None,
    observer: InvocationObserver | None = None,
) -> AsyncIterator[tuple[ApprovalRoom, Path]]:
    """Run the cell's agent in a fresh room and workdir: in manual approval mode,
    or as ``build`` makes it, under ``session_config`` with its turns watched by
    ``observer``."""
    dialect = DIALECTS[Adapter(cell.adapter_id)]
    for dep in dialect.extra_deps:
        require_dep(dep, cell.settings)
    # OpenCode's turn-phase records are content-free INFO lines; a failing
    # cell's captured log then shows where its turn stopped. Scoped to that
    # package: other adapters log tool input at INFO.
    with (
        sdk_logs_at(band.adapters.opencode, logging.INFO),
        tempfile.TemporaryDirectory(
            prefix="band-e2e-approval-", dir=dialect.workdir_root(cell.settings)
        ) as workdir,
    ):
        # Resolved: a symlinked temp root (macOS /var) reads as an outside dir.
        root = Path(workdir).resolve()
        setup = AgentSetup(root, wait_timeout_s, approvers)
        adapter = (build or dialect.build)(cell.settings, setup)
        async with running_provisioned_agent(
            adapter,
            cell.resources,
            label=f"approval-{label}",
            session_config=session_config,
            observer=observer,
        ) as agent:
            room_id = await cell.resources.provision_room(
                title=f"e2e-approval-{cell.adapter_id}-{label}",
                participants=[agent.id],
            )
            async with reply_capture(room_id) as capture:
                yield (
                    ApprovalRoom(
                        agent,
                        Adapter(cell.adapter_id),
                        room_id,
                        capture,
                        dialect,
                        user_ops,
                        budget,
                    ),
                    root,
                )


def _wait_timeout_s(outcome: Outcome) -> float:
    """The adapter's approval wait: expiring for TIMEOUT, else outlasting both
    barriers (and still under Cursor's default turn timeout, which it must be)."""
    return EXPIRING_WAIT_S if outcome is Outcome.TIMEOUT else BUDGET.deadline_s * 2
