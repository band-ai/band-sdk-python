"""Band/custom tool bridging: execution, reporting, and validation failures."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.core.types import AdapterFeatures, Emit
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolInvocation


class TestToolBridging:
    @pytest.mark.asyncio
    async def test_band_tool_executes_and_reports_events(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.EXECUTION})
        )
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        handler = client.sessions[0].find_tool("band_get_participants").handler
        result = await handler(
            ToolInvocation(
                tool_call_id="call-9", tool_name="band_get_participants", arguments={}
            )
        )

        assert result.result_type == "success"
        assert tools.tool_calls[-1]["tool_name"] == "band_get_participants"
        event_types = [e["message_type"] for e in tools.events_sent]
        assert "tool_call" in event_types
        assert "tool_result" in event_types

    @pytest.mark.asyncio
    async def test_band_send_message_tool_call_is_reported(self):
        """band_send_message is reported like any other tool — no suppression."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.EXECUTION})
        )
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        handler = client.sessions[0].find_tool("band_send_message").handler
        await handler(
            ToolInvocation(
                tool_call_id="call-1",
                tool_name="band_send_message",
                arguments={"content": "hi", "mentions": []},
            )
        )

        event_types = [e["message_type"] for e in tools.events_sent]
        assert "tool_call" in event_types
        assert "tool_result" in event_types

    @pytest.mark.asyncio
    async def test_no_execution_events_when_emit_disabled(self):
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        handler = client.sessions[0].find_tool("band_get_participants").handler
        await handler(
            ToolInvocation(
                tool_call_id="call-9", tool_name="band_get_participants", arguments={}
            )
        )

        assert not [
            e
            for e in tools.events_sent
            if e["message_type"] in ("tool_call", "tool_result")
        ]

    @pytest.mark.asyncio
    async def test_custom_tool_takes_precedence(self):
        class EchoInput(BaseModel):
            text: str

        async def echo(params: EchoInput) -> str:
            return f"echo: {params.text}"

        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, additional_tools=[(EchoInput, echo)]
        )
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        session = client.sessions[0]
        assert "echo" in [t.name for t in session.kwargs["tools"]]
        result = await session.find_tool("echo").handler(
            ToolInvocation(
                tool_call_id="c1", tool_name="echo", arguments={"text": "hi"}
            )
        )

        assert result.text_result_for_llm == "echo: hi"
        assert not tools.tool_calls  # platform executor not used

    @pytest.mark.asyncio
    async def test_invalid_custom_tool_args_return_llm_readable_failure(self):
        class EchoInput(BaseModel):
            text: str

        async def echo(params: EchoInput) -> str:
            return params.text

        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, additional_tools=[(EchoInput, echo)]
        )
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        result = (
            await client.sessions[0]
            .find_tool("echo")
            .handler(
                ToolInvocation(
                    tool_call_id="c1", tool_name="echo", arguments={"wrong": 1}
                )
            )
        )

        assert result.result_type == "failure"
        assert "echo" in result.text_result_for_llm

    @pytest.mark.asyncio
    async def test_model_level_validation_error_is_llm_readable(self):
        """Model-validator errors have loc=() and must not crash the handler."""
        from pydantic import model_validator

        class PairInput(BaseModel):
            a: int
            b: int

            @model_validator(mode="after")
            def check_order(self) -> "PairInput":
                if self.a >= self.b:
                    raise ValueError("a must be less than b")
                return self

        async def pair(params: PairInput) -> str:
            return "ok"

        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, additional_tools=[(PairInput, pair)]
        )
        tools = ToolSchemaFakeTools()
        await run_message(adapter, tools)

        result = (
            await client.sessions[0]
            .find_tool("pair")
            .handler(
                ToolInvocation(
                    tool_call_id="c1", tool_name="pair", arguments={"a": 2, "b": 1}
                )
            )
        )

        assert result.result_type == "failure"
        assert "a must be less than b" in result.text_result_for_llm

    @pytest.mark.asyncio
    async def test_handler_uses_current_room_tools(self):
        """Handlers must resolve tools at call time, not capture turn-1 tools."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client)
        first_tools = ToolSchemaFakeTools()
        await run_message(adapter, first_tools)

        second_tools = ToolSchemaFakeTools()
        await run_message(adapter, second_tools, is_session_bootstrap=False)

        handler = client.sessions[0].find_tool("band_get_participants").handler
        await handler(
            ToolInvocation(
                tool_call_id="c1", tool_name="band_get_participants", arguments={}
            )
        )

        assert not first_tools.tool_calls
        assert second_tools.tool_calls
