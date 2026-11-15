"""Native OMP host-tool calls are narrated into the Band room."""

from __future__ import annotations

import pytest

from band.core.types import MessageType
from band.runtime.tools.types import BandTool
from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestOmpToolNarration:
    @with_adapters(Adapter.OMP, **TOOL_AGENT)
    @pytest.mark.timeout(extra=180)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_omp_band_tool_call_is_narrated(
        self,
        agent: ProvisionedAgent,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        marker = unique_marker("ompevent")
        room_id = await resource_manager.provision_room(
            title="e2e-omp-tool-call", participants=[agent.id]
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
        tool_call_events.assert_contains_any([BandTool.SEND_EVENT])
