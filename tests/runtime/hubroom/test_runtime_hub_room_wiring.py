"""The runtime forwards the hub-room id to the executions it builds."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.tools import (
    AgentTools,
)


class TestRuntimeHubRoomWiring:
    def test_set_hub_room_id_propagates_to_new_executions(self) -> None:
        """AgentRuntime.set_hub_room_id is forwarded to ExecutionContext."""
        link = MagicMock()
        link.rest = MagicMock()
        on_execute = AsyncMock()
        runtime = AgentRuntime(link=link, agent_id="agent-1", on_execute=on_execute)

        runtime.set_hub_room_id("hub-room-xyz")
        assert runtime._hub_room_id == "hub-room-xyz"

        # Build an ExecutionContext the way the runtime admits a room
        ctx = ExecutionContext(
            room_id="hub-room-xyz",
            link=link,
            on_execute=on_execute,
            hub_room_id=runtime._hub_room_id,
        )
        assert ctx.hub_room_id == "hub-room-xyz"

        # AgentTools.from_context picks it up
        tools = AgentTools.from_context(ctx)
        assert tools.is_hub_room is True
