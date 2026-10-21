from __future__ import annotations

from typing import Any, ClassVar

from band.adapters.agno import (
    _bind_room_tools,
)
from tests.adapters.agno.helpers import (
    SchemaTools,
    openai_tool_schema,
)


class TestFeatureFilters:
    """include_tools/exclude_tools/include_categories gate which Band tools
    are wired (parity with LangGraph)."""

    ALL_SCHEMAS: ClassVar[list[dict[str, Any]]] = [
        openai_tool_schema("band_send_message"),  # chat
        openai_tool_schema("band_lookup_peers"),  # chat
        openai_tool_schema("band_store_memory"),  # memory
        openai_tool_schema("band_add_contact"),  # contacts
    ]

    async def _resolved_names(self, adapter) -> list[str]:
        tools = SchemaTools(self.ALL_SCHEMAS)
        with _bind_room_tools(tools):
            resolved = await adapter._resolve_room_tools()
        return [fn.name for fn in resolved]

    async def test_include_tools_keeps_only_named(self, make_started_adapter):
        adapter, _ = await make_started_adapter(include_tools=["band_send_message"])

        assert await self._resolved_names(adapter) == ["band_send_message"]

    async def test_exclude_tools_drops_named(self, make_started_adapter):
        adapter, _ = await make_started_adapter(exclude_tools=["band_send_message"])

        names = await self._resolved_names(adapter)
        assert "band_send_message" not in names
        assert "band_lookup_peers" in names

    async def test_include_categories_keeps_only_category(self, make_started_adapter):
        adapter, _ = await make_started_adapter(include_categories=["chat"])

        assert sorted(await self._resolved_names(adapter)) == [
            "band_lookup_peers",
            "band_send_message",
        ]
