"""Real ACP decoding with an explicit native-registration identity resolver."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from acp.schema import ToolCallProgress, ToolCallStart

from band.core.protocols import AgentToolsProtocol
from band.core.types import Emit
from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.testing.activity import ActivityRecorder
from tests.integrations.acp.clientbehavior.kit import observed_tools


async def collect_native_activity(
    updates: Sequence[ToolCallStart | ToolCallProgress], *, emit: tuple[Emit, ...] = ()
) -> ActivityRecorder:
    recorder = ActivityRecorder()
    client = ACPCollectingClient()
    client.set_sink(
        "native",
        RoomTurnEmitter(
            cast(AgentToolsProtocol, observed_tools(recorder)),
            emit=frozenset(emit),
            activity_name=lambda call: call.mcp_tool or UNKNOWN_ACTIVITY_TOOL_NAME,
        ),
    )
    for update in updates:
        await client.session_update("native", update)
    return recorder
