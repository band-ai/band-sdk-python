from __future__ import annotations

from band.adapters.agno import (
    _bind_room_tools,
)
from tests.adapters.agno.helpers import (
    ContactAwareTools,
    SchemaTools,
)


class TestHubContactExposure:
    """The adapter decides contact exposure (mirrors LangGraph): the CONTACTS
    capability OR a hub room force-includes contact tool schemas, resolved per
    run so visibility is strictly per-room."""

    async def test_normal_room_does_not_request_contacts(self, make_started_adapter):
        adapter, _ = await make_started_adapter()
        tools = SchemaTools([], room_id="room-A")

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": False}
        ]

    async def test_hub_room_forces_contacts(self, make_started_adapter):
        adapter, _ = await make_started_adapter()
        tools = SchemaTools([], hub_room_id="hub", room_id="hub")

        with _bind_room_tools(tools):
            await adapter._resolve_room_tools()

        assert tools.schema_calls == [
            {"include_memory": False, "include_contacts": True}
        ]

    async def test_contacts_do_not_leak_into_normal_room_after_hub(
        self, make_started_adapter
    ):
        # Core regression: after a hub room exposes contact tools, a subsequent
        # normal room's resolution must NOT include them. The old additive wiring
        # accumulated the union on the shared agent; per-run resolution does not.
        adapter, _ = await make_started_adapter()

        hub = ContactAwareTools(hub_room_id="hub", room_id="hub")
        with _bind_room_tools(hub):
            hub_names = [fn.name for fn in await adapter._resolve_room_tools()]
        assert "band_add_contact" in hub_names

        normal = ContactAwareTools(room_id="room-A")
        with _bind_room_tools(normal):
            normal_names = [fn.name for fn in await adapter._resolve_room_tools()]

        assert normal_names == ["band_send_message"]
        assert "band_add_contact" not in normal_names
