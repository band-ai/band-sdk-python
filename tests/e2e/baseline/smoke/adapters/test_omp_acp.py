"""Live smoke coverage for OMP over ACP."""

from __future__ import annotations

import pytest

from band.core.types import MessageType
from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

BAND_EVENT_TOOL_NAME = "band_send_event"


@with_adapters(Adapter.OMP_ACP, **TOOL_AGENT)
@flaky_model("OMP may occasionally miss an explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_omp_acp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("omp-acp-event")
    room_id = await resource_manager.provision_room(
        title="e2e-omp-acp-tool-call", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_call_events = await capture.events(
            MessageType.TOOL_CALL, sender_id=agent.id
        )

    thoughts.assert_contains_any([marker])
    tool_call_events.assert_at_least(1)
    tool_call_events.assert_contains_any([BAND_EVENT_TOOL_NAME])
