"""PortableCustomToolDef behavior for Pydantic AI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, Field

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.pydanticai.kit import (
    _portable_tool_agent,
    _run_portable_tool,
    _scripted_tool_calls,
)
from tests.framework_conformance.turnprobes import CUSTOM_TOOL_DECLARATIONS


class TestPortableCustomToolDef:
    """pydantic accepts the portable CustomToolDef (InputModel, handler) tuple form —
    the same custom-tool shape anthropic/crewai/claude_sdk/langgraph take."""

    @pytest.mark.asyncio
    async def test_tuple_is_normalized_to_a_named_callable(self):

        class LookupInput(BaseModel):
            """look up a code."""

            key: str

        def lookup(args: LookupInput) -> str:
            return f"code:{args.key}"

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"),
            additional_tools=[(LookupInput, lookup)],
        )
        # Normalized to a native callable named from the model (not the handler).
        assert [t.__name__ for t in adapter._custom_tools] == ["lookup"]

    def test_converted_tuple_flattens_in_pydantic_ai(self):

        class LookupInput(BaseModel):
            """look up a code."""

            key: str

        def lookup(args: LookupInput) -> str:
            return f"code:{args.key}"

        (tool,) = _portable_tool_agent(
            (LookupInput, lookup)
        )._function_toolset.tools.values()
        schema = tool.function_schema.json_schema
        # pydantic-ai flattens the single model param into the tool's args.
        assert tool.name == "lookup"
        assert sorted((schema.get("properties") or {}).keys()) == ["key"]

    @pytest.mark.asyncio
    async def test_async_handler_tuple_is_awaited_end_to_end(self):
        """An async CustomToolDef handler returns its awaited value through a real
        pydantic-ai run — not an unawaited coroutine (which the previous sync
        passthrough produced, failing serialization)."""

        class LookupInput(BaseModel):
            """look up a code."""

            key: str

        async def lookup(args: LookupInput) -> str:
            return f"code:{args.key}"

        (content,) = await _run_portable_tool((LookupInput, lookup))
        assert isinstance(content, str)
        assert content.startswith("code:")

    @pytest.mark.asyncio
    async def test_zero_arg_handler_tuple_runs_end_to_end(self):
        """A zero-argument handler with an empty InputModel executes through a real
        pydantic-ai run — the previous sync passthrough called it with one
        positional arg and raised TypeError."""

        class PingInput(BaseModel):
            """ping."""

        def ping() -> str:
            return "pong"

        assert await _run_portable_tool((PingInput, ping)) == ["pong"]

    @pytest.mark.asyncio
    async def test_aliased_input_model_runs_end_to_end(self):
        """An InputModel using a field alias executes through a real pydantic-ai
        run — a dump/re-validate round-trip would emit field names and fail
        re-validation against the alias-only model."""

        class AliasedInput(BaseModel):
            """look up a user."""

            user_id: str = Field(alias="userId")

        def lookup(args: AliasedInput) -> str:
            return f"user:{args.user_id}"

        (content,) = await _run_portable_tool((AliasedInput, lookup))
        assert isinstance(content, str)
        assert content.startswith("user:")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
    async def test_custom_tool_records_its_effect_on_the_room_turn(
        self, declare: Callable[..., Any], complete: bool
    ):
        class FileInput(BaseModel):
            """File the report."""

            note: str

        @declare
        def file_report(args: FileInput) -> str:
            return "filed"

        tools = FakeAgentTools()
        adapter = PydanticAIAdapter(
            llm=_scripted_tool_calls(("file", {"note": "go"})),
            additional_tools=[(FileInput, file_report)],
        )
        adapter.agent_name = "TestBot"

        await adapter._create_agent().run("go", deps=tools)

        assert tools.turn.complete is complete
