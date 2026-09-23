"""Conformance tests for task-board tool infrastructure.

These tests verify that all 7 task-board tools are properly registered in
the shared infrastructure that all adapters depend on: TOOL_MODELS, schema
generators, and execute_tool_call dispatch. Mirrors
test_contact_tool_conformance.py, with one difference: Capability.TASKS is
not in DEFAULT_CAPABILITIES (unlike CONTACTS), so the schema tests must pass
it explicitly -- the no-argument get_tool_schemas() call the contact test
uses would silently resolve to {CONTACTS} and exclude every task tool.
"""

from __future__ import annotations

import pytest

from band.core.types import Capability
from band.runtime.tools import TOOL_MODELS, AgentTools

TASK_TOOL_NAMES = [
    "band_list_tasks",
    "band_create_task",
    "band_get_task",
    "band_update_task",
    "band_get_task_history",
    "band_get_board",
    "band_set_board",
]


class TestTaskToolModels:
    """Task tools must be registered in TOOL_MODELS."""

    @pytest.mark.parametrize("tool_name", TASK_TOOL_NAMES)
    def test_task_tool_in_tool_models(self, tool_name: str) -> None:
        assert tool_name in TOOL_MODELS, (
            f"{tool_name} missing from TOOL_MODELS registry"
        )

    @pytest.mark.parametrize("tool_name", TASK_TOOL_NAMES)
    def test_task_tool_model_has_docstring(self, tool_name: str) -> None:
        model = TOOL_MODELS[tool_name]
        assert model.__doc__, (
            f"{tool_name} model has no docstring (used as LLM description)"
        )


class TestTaskToolSchemas:
    """Task tools must appear in generated schemas when Capability.TASKS is requested."""

    @pytest.fixture()
    def agent_tools(self) -> AgentTools:
        """Create AgentTools with a mock REST client (schemas don't need API)."""
        return AgentTools(room_id="test-room", rest=None, participants=[])  # type: ignore[arg-type]

    @pytest.mark.parametrize("tool_name", TASK_TOOL_NAMES)
    def test_task_tool_in_anthropic_schemas(
        self, agent_tools: AgentTools, tool_name: str
    ) -> None:
        schemas = agent_tools.get_tool_schemas(
            "anthropic", capabilities=frozenset({Capability.TASKS})
        )
        tool_names = [s["name"] for s in schemas]
        assert tool_name in tool_names, (
            f"{tool_name} missing from Anthropic schema output"
        )

    @pytest.mark.parametrize("tool_name", TASK_TOOL_NAMES)
    def test_task_tool_in_openai_schemas(
        self, agent_tools: AgentTools, tool_name: str
    ) -> None:
        schemas = agent_tools.get_tool_schemas(
            "openai", capabilities=frozenset({Capability.TASKS})
        )
        tool_names = [s["function"]["name"] for s in schemas]
        assert tool_name in tool_names, f"{tool_name} missing from OpenAI schema output"

    def test_task_tools_excluded_without_capability(
        self, agent_tools: AgentTools
    ) -> None:
        """Capability.TASKS is opt-in, unlike CONTACTS -- the default call must
        exclude task tools rather than silently including them."""
        schemas = agent_tools.get_tool_schemas("anthropic")
        tool_names = {s["name"] for s in schemas}
        assert not tool_names & set(TASK_TOOL_NAMES)


class TestTaskToolDispatch:
    """Task tools must have dispatch entries in execute_tool_call."""

    @pytest.mark.parametrize("tool_name", TASK_TOOL_NAMES)
    @pytest.mark.asyncio
    async def test_task_tool_dispatch_key_exists(self, tool_name: str) -> None:
        """Calling an unknown tool returns 'Unknown tool: ...',
        so a task tool must NOT return that prefix."""
        tools = AgentTools(room_id="test-room", rest=None, participants=[])  # type: ignore[arg-type]
        # Pass empty args - we expect a validation or execution error,
        # but NOT "Unknown tool: ..."
        result = await tools.execute_tool_call(tool_name, {})
        assert not isinstance(result, str) or not result.startswith("Unknown tool:"), (
            f"{tool_name} has no dispatch entry in execute_tool_call"
        )
