"""OpenCode MCP registration honors AdapterFeatures.exclude_tools."""

from __future__ import annotations

from band.adapters.opencode import OpencodeAdapter
from band.core.types import Capability
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import MEMORY_TOOL_NAMES, BandTool
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    idle_client,
    make_platform_message,
    tools_protocol,
)
from tests.mcpbackends import backends_created_by


class TestOpencodeToolFilters:
    async def test_exclude_tools_are_absent_from_mcp_definitions(self) -> None:
        """exclude_tools must drop those names from the MCP backend settings
        while leaving capability-enabled platform tools registered."""
        excluded = {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        with backends_created_by() as starts:
            adapter = OpencodeAdapter(
                client_factory=lambda _config: idle_client(),
                capabilities={Capability.MEMORY, Capability.CONTACTS},
                exclude_tools=excluded,
            )
            await adapter.on_started("OpenCode Agent", "A coding agent")
            await deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(FakeAgentTools(participants=ROOM_MEMBERS)),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
            await adapter.on_cleanup("room-1")

        (settings,) = starts.requested
        names = {definition.name for definition in settings.tool_definitions}
        assert names.isdisjoint(excluded)
        assert MEMORY_TOOL_NAMES <= names
