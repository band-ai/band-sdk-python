"""Conformance tests for contact tool infrastructure.

These tests verify that all 5 contact tools are properly registered in the
shared infrastructure that all adapters depend on: TOOL_MODELS, schema
generators, and execute_tool_call dispatch.
"""

from __future__ import annotations

import pytest

from band.runtime.tools import TOOL_MODELS, AgentTools

CONTACT_TOOL_NAMES = [
    "band_list_contacts",
    "band_add_contact",
    "band_remove_contact",
    "band_list_contact_requests",
    "band_respond_contact_request",
]


class TestContactToolModels:
    """Contact tools must be registered in TOOL_MODELS."""

    @pytest.mark.parametrize("tool_name", CONTACT_TOOL_NAMES)
    def test_contact_tool_in_tool_models(self, tool_name: str) -> None:
        assert tool_name in TOOL_MODELS, (
            f"{tool_name} missing from TOOL_MODELS registry"
        )

    @pytest.mark.parametrize("tool_name", CONTACT_TOOL_NAMES)
    def test_contact_tool_model_has_docstring(self, tool_name: str) -> None:
        model = TOOL_MODELS[tool_name]
        assert model.__doc__, (
            f"{tool_name} model has no docstring (used as LLM description)"
        )


class TestContactToolSchemas:
    """Contact tools must appear in generated schemas."""

    @pytest.fixture()
    def agent_tools(self) -> AgentTools:
        """Create AgentTools with a mock REST client (schemas don't need API)."""
        return AgentTools(room_id="test-room", rest=None, participants=[])  # type: ignore[arg-type]

    @pytest.mark.parametrize("tool_name", CONTACT_TOOL_NAMES)
    def test_contact_tool_in_anthropic_schemas(
        self, agent_tools: AgentTools, tool_name: str
    ) -> None:
        schemas = agent_tools.get_tool_schemas("anthropic")
        tool_names = [s["name"] for s in schemas]
        assert tool_name in tool_names, (
            f"{tool_name} missing from Anthropic schema output"
        )

    @pytest.mark.parametrize("tool_name", CONTACT_TOOL_NAMES)
    def test_contact_tool_in_openai_schemas(
        self, agent_tools: AgentTools, tool_name: str
    ) -> None:
        schemas = agent_tools.get_tool_schemas("openai")
        tool_names = [s["function"]["name"] for s in schemas]
        assert tool_name in tool_names, f"{tool_name} missing from OpenAI schema output"


class TestContactToolDispatch:
    """Contact tools must have dispatch entries in execute_tool_call."""

    @pytest.mark.parametrize("tool_name", CONTACT_TOOL_NAMES)
    @pytest.mark.asyncio
    async def test_contact_tool_dispatch_key_exists(self, tool_name: str) -> None:
        """Calling an unknown tool returns 'Unknown tool: ...',
        so a contact tool must NOT return that prefix."""
        tools = AgentTools(room_id="test-room", rest=None, participants=[])  # type: ignore[arg-type]
        # Pass empty args - we expect a validation or execution error,
        # but NOT "Unknown tool: ..."
        result = await tools.execute_tool_call(tool_name, {})
        assert not isinstance(result, str) or not result.startswith("Unknown tool:"), (
            f"{tool_name} has no dispatch entry in execute_tool_call"
        )
