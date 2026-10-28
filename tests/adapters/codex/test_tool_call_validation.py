"""Codex adapter: a tool-call validation failure."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.runtime.tools import BandTool, ToolCallOutcome
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestToolCallValidation:
    @pytest.mark.asyncio
    async def test_tool_call_validation_error_returns_friendly_message(self) -> None:
        """A base-tool arg-validation failure returns a user-friendly error.

        AgentTools catches base-tool validation INSIDE execute_tool_call_structured and
        returns ok=False with a friendly message (it does not raise), so the adapter
        surfaces it via the ok=False path.
        """

        class ValidationErrorTools(ToolSchemaFakeTools):
            async def execute_tool_call_structured(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolCallOutcome:
                self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
                return ToolCallOutcome(
                    value="Invalid arguments for band_send_message: content: Field required",
                    ok=False,
                    error_message="content: Field required",
                )

        events = [
            tool_call_request(99, BandTool.SEND_MESSAGE),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ValidationErrorTools()

        await adapter.on_started("Bot", "desc")
        await send_bootstrap(adapter, tools=tools)

        # The adapter should have responded to the tool call with success=False
        error_responses = [
            (rid, payload)
            for rid, payload in fake_client.responses
            if payload.get("success") is False
        ]
        assert len(error_responses) == 1
        error_text = error_responses[0][1]["contentItems"][0]["text"]
        assert "Invalid arguments for band_send_message" in error_text
