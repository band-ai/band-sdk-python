from __future__ import annotations

from band.adapters.agno import (
    AgnoAdapter,
    _bind_room_tools,
)
from band.core.types import Capability
from tests.adapters.agno.helpers import (
    SchemaTools,
    openai_tool_schema,
)


class TestRoomToolResolution:
    """Band tools are exposed per-run via the ``_resolve_room_tools`` factory
    Agno calls each turn, not wired onto the agent. These pin what that factory
    returns and the schema requests it makes for the active room."""

    async def test_resolves_band_tools_for_active_room(self, make_started_adapter):
        tools = SchemaTools(
            [
                openai_tool_schema("band_send_message"),
                openai_tool_schema("band_lookup_peers"),
            ]
        )
        adapter, _ = await make_started_adapter()

        with _bind_room_tools(tools):
            resolved = await adapter._resolve_room_tools()

        assert [fn.name for fn in resolved] == [
            "band_send_message",
            "band_lookup_peers",
        ]

    async def test_no_band_tools_outside_a_bound_room(self, make_started_adapter):
        # Defensive: with no active room bound, the factory exposes no Band tools
        # (and does not even request schemas) rather than guessing visibility.
        tools = SchemaTools([openai_tool_schema("band_send_message")])
        adapter, _ = await make_started_adapter()

        resolved = await adapter._resolve_room_tools()  # no _bind_room_tools

        assert resolved == []
        assert tools.schema_calls == []

    async def test_capability_flags_drive_schema_request(self, make_started_adapter):
        tools = SchemaTools([])
        adapter, _ = await make_started_adapter(
            capabilities={Capability.MEMORY, Capability.CONTACTS}
        )

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": True, "include_contacts": True}
        ]

    async def test_schema_build_is_cached_across_runs(self, make_started_adapter):
        # Same contact flag across runs -> schemas are built once and reused,
        # not rebuilt every turn.
        tools = SchemaTools([openai_tool_schema("band_send_message")])
        adapter, _ = await make_started_adapter()

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()
            await adapter._resolve_room_tools()
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": False}
        ]

    async def test_user_tools_are_reincluded(self, make_agno_agent):
        # Replacing agent.tools with our factory must not drop the user's own
        # tools; they are re-included alongside the room's Band tools.
        user_tool = object()
        agent = make_agno_agent()
        agent.tools = [user_tool]
        adapter = AgnoAdapter(agent=agent)
        await adapter.on_started("TestBot", "desc")

        tools = SchemaTools([openai_tool_schema("band_send_message")])
        with _bind_room_tools(tools):
            resolved = await adapter._resolve_room_tools()

        assert resolved[0] is user_tool
        assert [getattr(t, "name", None) for t in resolved[1:]] == ["band_send_message"]
