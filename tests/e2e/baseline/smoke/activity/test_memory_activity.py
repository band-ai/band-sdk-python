"""Saving a memory reports memory activity, then the turn clears."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures, Capability
from band.runtime.activity import MEMORY
from tests.e2e.baseline.agents import (
    Adapter,
    ExcludedAdapter,
    not_applicable,
    per_adapter,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT_SYSTEM_PROMPT,
    store_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.adapters import specs
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=4)
SAVING = MEMORY.model_dump(exclude_none=True)
MEMORY_ADAPTERS = tuple(spec.id for spec in specs(supports={Capability.MEMORY}))
NOT_SAVING = (
    *not_applicable(MEMORY_ADAPTERS, "registers no Band memory capability"),
    ExcludedAdapter(
        Adapter.NOOA,
        "saves memory from inside execute_python, which the room sees instead",
    ),
)


class TestMemoryActivity:
    @per_adapter(exclude=NOT_SAVING)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_storing_a_memory_reports_memory_and_clears(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
    ) -> None:
        identity = await cell.provision()
        room = await cell.resources.provision_room(
            title="e2e-activity-memory", participants=[identity.id]
        )
        observed = ActivityCapture(deadline_s=BUDGET.deadline_s)
        marker = unique_marker("memact")
        async with (
            observed.rooms(baseline_ws, room),
            reply_capture(room, deadline_s=BUDGET.deadline_s) as capture,
            cell.run_as_with_handle(
                identity,
                prompt=TOOL_AGENT_SYSTEM_PROMPT,
                features=AdapterFeatures(
                    emit=frozenset(), capabilities={Capability.MEMORY}
                ),
                link_factory=observed.link,
            ),
        ):
            since = observed.cursor()
            sent = await user_ops.send_message(
                room,
                store_memory_instruction(marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            saved = await observed.report(
                room, agent_id=identity.id, working=True, activity=SAVING, since=since
            )
            await capture.wait_for_processed(sent, identity.id)
            await observed.finished(room, identity.id, since=saved.sequence)
            observed.assert_private(marker)
