from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    StrandsAdapter,
    StrandsAdapterConfig,
)
from band.core.types import (
    Capability,
)
from band.runtime.tools import get_tool_description
from band.testing import (
    FakeAgentTools,
)


class TestToolRegistration:
    @pytest.mark.asyncio
    async def test_base_tools_only_by_default(self):
        adapter = StrandsAdapter(StrandsAdapterConfig(model="m"))
        await adapter.on_started("Bot", "A bot")

        names = {t.tool_name for t in adapter._build_platform_tools(FakeAgentTools())}
        assert names == {
            "band_send_message",
            "band_send_event",
            "band_no_reply",
            "band_add_participant",
            "band_remove_participant",
            "band_lookup_peers",
            "band_get_participants",
            "band_create_chatroom",
        }

    @pytest.mark.asyncio
    async def test_capability_gated_tools_registered(self):
        adapter = StrandsAdapter(
            StrandsAdapterConfig(model="m"),
            capabilities=Capability.MEMORY | Capability.CONTACTS,
        )
        await adapter.on_started("Bot", "A bot")

        names = {t.tool_name for t in adapter._build_platform_tools(FakeAgentTools())}
        assert {"band_list_contacts", "band_respond_contact_request"} <= names
        assert {"band_store_memory", "band_archive_memory"} <= names

    @pytest.mark.asyncio
    async def test_platform_tool_descriptions_from_registry(self):

        adapter = StrandsAdapter(StrandsAdapterConfig(model="m"))
        await adapter.on_started("Bot", "A bot")

        by_name = {
            tool.tool_name: tool
            for tool in adapter._build_platform_tools(FakeAgentTools())
        }
        assert by_name["band_send_message"].tool_spec[
            "description"
        ] == get_tool_description("band_send_message")

    @pytest.mark.asyncio
    async def test_excluded_tools_never_reach_the_model(self):
        """Reaching a tool is enough to execute it, so a filter must apply here."""
        adapter = StrandsAdapter(
            StrandsAdapterConfig(model="m"), exclude_tools=["band_remove_participant"]
        )
        await adapter.on_started("Bot", "A bot")

        names = {t.tool_name for t in adapter._build_platform_tools(FakeAgentTools())}

        assert "band_remove_participant" not in names
        assert "band_send_message" in names

    @pytest.mark.asyncio
    async def test_each_turns_tools_own_their_input_schema(self):
        """Strands normalizes a tool spec by writing into its nested schema.

        Tools are rebuilt per turn, so a schema shared between turns would carry
        one turn's framework normalization into the next.
        """
        adapter = StrandsAdapter(StrandsAdapterConfig(model="m"))
        await adapter.on_started("Bot", "A bot")

        def send_properties(turn_tools: list) -> dict:
            by_name = {tool.tool_name: tool for tool in turn_tools}
            return by_name["band_send_message"].tool_spec["inputSchema"]["json"][
                "properties"
            ]

        first = send_properties(adapter._build_platform_tools(FakeAgentTools()))
        second = send_properties(adapter._build_platform_tools(FakeAgentTools()))
        first["content"]["normalized"] = "written by the framework"

        assert "normalized" not in second["content"]
