"""ToolBridge behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.types import ALL_CAPABILITIES
from band.runtime.tools import AgentTools
from band.runtime.tools.schema import ToolCallOutcome
from tests.adapters.googleadk.bridge import _BandToolBridge


class TestToolBridge:
    """Tests for _BandToolBridge."""

    def test_tool_name(self):
        """Should return the tool name."""
        bridge = _BandToolBridge(
            tool_name="band_send_message",
            tool_description="Send a message",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        assert bridge.name == "band_send_message"

    def test_tool_description(self):
        """Should return the tool description."""
        bridge = _BandToolBridge(
            tool_name="test_tool",
            tool_description="A test tool",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        assert bridge.description == "A test tool"

    def test_get_declaration(self):
        """Should build a FunctionDeclaration with stripped additionalProperties."""
        bridge = _BandToolBridge(
            tool_name="my_tool",
            tool_description="Does things",
            parameters_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            },
            tools=MagicMock(),
            custom_tools=[],
        )

        decl = bridge._get_declaration()

        assert decl.name == "my_tool"
        assert decl.description == "Does things"
        # additionalProperties should be stripped for Gemini compatibility
        assert "additionalProperties" not in (decl.parameters or {})

    def test_declaration_eagerly_cached(self):
        """Declaration should be built eagerly in __init__ and cached."""
        bridge = _BandToolBridge(
            tool_name="cached_tool",
            tool_description="Cached",
            parameters_schema={"type": "object", "properties": {}},
            tools=MagicMock(),
            custom_tools=[],
        )

        # _cached_declaration should exist from __init__
        assert hasattr(bridge, "_cached_declaration")
        assert bridge._cached_declaration.name == "cached_tool"

        # _build_declaration should return the same cached object
        assert bridge._build_declaration() is bridge._cached_declaration

        # _get_declaration should delegate to _build_declaration
        assert bridge._get_declaration() is bridge._cached_declaration

    @pytest.mark.asyncio
    async def test_executes_platform_tool(self):
        """Should delegate to AgentToolsProtocol."""
        mock_tools = MagicMock()
        mock_tools.execute_tool_call_structured = AsyncMock(
            return_value=ToolCallOutcome(value={"status": "sent"}, ok=True)
        )

        bridge = _BandToolBridge(
            tool_name="band_send_message",
            tool_description="Send a message",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[],
        )

        result = await bridge.run_async(
            args={"content": "Hello", "mentions": ["@alice"]},
            tool_context=MagicMock(),
        )

        mock_tools.execute_tool_call_structured.assert_called_once_with(
            "band_send_message", {"content": "Hello", "mentions": ["@alice"]}
        )
        assert result == '{"status": "sent"}'

    @pytest.mark.asyncio
    async def test_handles_tool_error(self):
        """Should return error string on tool failure."""
        mock_tools = MagicMock()
        mock_tools.execute_tool_call_structured = AsyncMock(
            side_effect=Exception("Tool failed!")
        )

        bridge = _BandToolBridge(
            tool_name="failing_tool",
            tool_description="Fails",
            parameters_schema={},
            tools=mock_tools,
            custom_tools=[],
        )

        result = await bridge.run_async(args={}, tool_context=MagicMock())
        assert result == {
            "ok": False,
            "error": "Error executing failing_tool: Tool failed!",
        }

    def test_builds_declaration_for_every_platform_tool(self):
        """Every platform tool's schema must build a ref-free Gemini declaration.

        Enum-typed params (e.g. band_list_memories'/band_store_memory's scope,
        system, type) are emitted by Pydantic as JSON-Schema ``$ref``/``$defs``.
        Gemini's ``parameters`` field is the restricted OpenAPI Schema subset and
        rejects those references, so the bridge must dereference them. This builds
        a bridge for *every* real platform tool (memory + contacts included) and
        asserts the cached declaration carries no leftover ``$ref``/``$defs`` — the
        gap that only surfaced at E2E when memory tools were enabled.
        """
        # Real platform schemas, link-free (schema building reads TOOL_DEFINITIONS,
        # not the REST client), with every optional tool category included.
        schemas = AgentTools("room-1", MagicMock()).get_openai_tool_schemas(
            capabilities=ALL_CAPABILITIES
        )
        names = {s["function"]["name"] for s in schemas}

        # Guard: the test is only meaningful if the ref-bearing tools are present,
        # so it can't silently pass if schema generation stops emitting them.
        assert {"band_list_memories", "band_store_memory"} <= names, (
            f"expected ref-bearing memory tools in schema set, got {sorted(names)}"
        )

        for schema in schemas:
            func = schema["function"]
            bridge = _BandToolBridge(
                tool_name=func["name"],
                tool_description=func.get("description", ""),
                parameters_schema=func.get("parameters", {}),
                tools=MagicMock(),
                custom_tools=[],
            )
            blob = bridge._get_declaration().model_dump_json()
            assert "$ref" not in blob and "$defs" not in blob, (
                f"{func['name']}: declaration leaked $ref/$defs (Gemini rejects them)"
            )
