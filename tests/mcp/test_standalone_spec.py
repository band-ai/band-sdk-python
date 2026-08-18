"""Tests for `band_mcp.server.standalone_spec` -- the CLI door's factory.

Covers the integration behavior test_registrar.py used to (scope/tools
filtering, duplicate-name detection, per-tool room classification, pinning)
against the new EngineSpec-based factory. The lower-level pieces it composes
-- extend_with_chat_id/pin_existing_chat_id (tests/mcp/test_engine.py),
classify_room_binding (tests/runtime/test_tools.py), StandaloneResolver
(tests/mcp/test_shared.py) -- have their own dedicated tests and are not
re-tested here; this file is about standalone_spec's own wiring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import build_engine
from band.runtime.tools import TOOL_DEFINITIONS, ToolDefinition, iter_tool_definitions
from band_mcp import server as server_mod
from band_mcp.config import Config, ConfigError
from band_mcp.server import standalone_spec
from band_mcp.shared import StandaloneResolver


def _spec_names(config: Config) -> set[str]:
    spec = standalone_spec(config, StandaloneResolver())
    return {registration.name for registration in spec.tools}


class TestScopeFiltering:
    def test_agent_only_registers_agent_surface(self) -> None:
        expected = {
            d.name
            for d in iter_tool_definitions(
                surface="agent", include_contacts=False, include_memory=False
            )
        }
        assert _spec_names(Config(scope=["agent"], tools=[])) == expected

    def test_human_only_registers_human_surface(self) -> None:
        expected = {
            d.name
            for d in iter_tool_definitions(
                surface="human", include_contacts=False, include_memory=False
            )
        }
        assert _spec_names(Config(scope=["human"], tools=[])) == expected

    def test_both_registers_union(self) -> None:
        expected: set[str] = set()
        for surface in ("agent", "human"):
            expected |= {
                d.name
                for d in iter_tool_definitions(
                    surface=surface, include_contacts=False, include_memory=False
                )
            }
        assert _spec_names(Config(scope=["agent", "human"], tools=[])) == expected

    def test_both_rejects_duplicate_names_across_surfaces(self, monkeypatch) -> None:
        agent_definition = TOOL_DEFINITIONS["band_create_chatroom"]
        human_definition = ToolDefinition(
            name=agent_definition.name,
            input_model=agent_definition.input_model,
            method_name=agent_definition.method_name,
            surface="human",
        )

        def fake_iter_tool_definitions(*, surface, include_contacts, include_memory):
            return [agent_definition] if surface == "agent" else [human_definition]

        monkeypatch.setattr(
            server_mod, "iter_tool_definitions", fake_iter_tool_definitions
        )

        with pytest.raises(
            ConfigError,
            match="Duplicate tool name across enabled surfaces: band_create_chatroom",
        ):
            standalone_spec(
                Config(scope=["agent", "human"], tools=[]), StandaloneResolver()
            )


class TestToolsGroups:
    def test_contacts_registers_contact_tools(self) -> None:
        names = _spec_names(Config(scope=["agent", "human"], tools=["contacts"]))
        assert "band_list_my_contacts" in names
        assert "band_resolve_handle" in names
        assert "band_list_memories" not in names
        assert "band_list_user_memories" not in names

    def test_memory_registers_memory_tools(self) -> None:
        names = _spec_names(Config(scope=["agent", "human"], tools=["memory"]))
        assert "band_list_memories" in names
        assert "band_list_user_memories" in names
        assert "band_list_my_contacts" not in names

    def test_both_registers_both_groups(self) -> None:
        names = _spec_names(
            Config(scope=["agent", "human"], tools=["contacts", "memory"])
        )
        assert "band_list_my_contacts" in names
        assert "band_list_memories" in names

    def test_empty_disables_both(self) -> None:
        names = _spec_names(Config(scope=["agent", "human"], tools=[]))
        assert "band_list_memories" not in names
        assert "band_list_my_contacts" not in names


class TestSchemaShape:
    def test_unpinned_agent_room_bound_tool_advertises_chat_id(self) -> None:
        spec = standalone_spec(Config(scope=["agent"], tools=[]), StandaloneResolver())
        registration = next(r for r in spec.tools if r.name == "band_send_message")
        schema = registration.input_model.model_json_schema()

        assert "chat_id" in schema["properties"]
        assert "chat_id" in schema["required"]

    def test_room_less_agent_tool_advertises_no_chat_id(self) -> None:
        spec = standalone_spec(Config(scope=["agent"], tools=[]), StandaloneResolver())
        registration = next(r for r in spec.tools if r.name == "band_create_chatroom")
        schema = registration.input_model.model_json_schema()

        assert "chat_id" not in schema.get("properties", {})

    def test_send_event_widened_to_five_message_types(self) -> None:
        spec = standalone_spec(Config(scope=["agent"], tools=[]), StandaloneResolver())
        registration = next(r for r in spec.tools if r.name == "band_send_event")
        schema = registration.input_model.model_json_schema()

        assert set(schema["properties"]["message_type"]["enum"]) == {
            "tool_call",
            "tool_result",
            "thought",
            "error",
            "task",
        }

    def test_pinned_agent_schema_hides_chat_id(self) -> None:
        spec = standalone_spec(
            Config(scope=["agent"], tools=[], room_id="r_pinned"), StandaloneResolver()
        )
        registration = next(r for r in spec.tools if r.name == "band_send_message")
        schema = registration.input_model.model_json_schema()

        assert "chat_id" not in schema.get("properties", {})

    def test_pinned_human_room_bound_schema_hides_chat_id(self) -> None:
        spec = standalone_spec(
            Config(scope=["human"], tools=[], room_id="r_pinned"), StandaloneResolver()
        )
        registration = next(
            r for r in spec.tools if r.name == "band_send_my_chat_message"
        )
        schema = registration.input_model.model_json_schema()

        assert "chat_id" not in schema.get("properties", {})

    def test_unpinned_human_room_bound_schema_includes_chat_id(self) -> None:
        spec = standalone_spec(Config(scope=["human"], tools=[]), StandaloneResolver())
        registration = next(
            r for r in spec.tools if r.name == "band_send_my_chat_message"
        )
        schema = registration.input_model.model_json_schema()

        assert "chat_id" in schema["properties"]

    @pytest.mark.parametrize("pin", [None, "r_pin"])
    @pytest.mark.parametrize("tool_name", ["band_list_my_chats", "band_get_my_profile"])
    def test_room_less_human_tools_schema_unchanged_by_pin(
        self, pin: str | None, tool_name: str
    ) -> None:
        spec = standalone_spec(
            Config(scope=["human"], tools=[], room_id=pin), StandaloneResolver()
        )
        registration = next(r for r in spec.tools if r.name == tool_name)
        assert "chat_id" not in registration.input_model.model_json_schema().get(
            "properties", {}
        )


async def test_pinned_agent_dispatch_ignores_client_sent_chat_id() -> None:
    """End-to-end through build_engine + a real dispatch: the pin
    unconditionally overrides a client-sent chat_id (verified against
    registrar.py's original guarantee)."""
    fake_agent_tools = MagicMock()
    fake_agent_tools.send_message = AsyncMock(return_value={"ok": True})
    resolver = StandaloneResolver()
    resolver._agent_tools_cache["r_pinned"] = fake_agent_tools

    spec = standalone_spec(
        Config(scope=["agent"], tools=[], room_id="r_pinned"), resolver
    )
    mcp = build_engine(spec)

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            "band_send_message",
            {"content": "hi", "mentions": ["@bob"], "chat_id": "r_ignored"},
        )
        assert not result.isError

    fake_agent_tools.send_message.assert_awaited_once_with(
        content="hi", mentions=["@bob"]
    )
