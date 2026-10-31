"""Observe normalized ACP calls without changing callback input contracts."""

from __future__ import annotations

from typing import Any

import pytest
from acp.helpers import start_tool_call
from acp.schema import ToolCallUpdate

from band.integrations.acp.collecting import ACPCollectingClient
from band.integrations.acp.permissions import cancel_permission
from band.integrations.acp.types import ACPToolCall


class TestACPToolObservation:
    @pytest.mark.parametrize(
        ("meta", "raw", "expected"),
        [
            (
                None,
                {"server": "band", "tool": "known", "arguments": {"x": 1}},
                ACPToolCall(
                    "call",
                    "band-known",
                    {"x": 1},
                    mcp_server="band",
                    mcp_tool="known",
                    title="native",
                ),
            ),
            (
                {"mcp": None},
                {"server": "band", "tool": "known", "arguments": {"x": 1}},
                ACPToolCall(
                    "call",
                    "band-known",
                    {"x": 1},
                    mcp_server="band",
                    mcp_tool="known",
                    title="native",
                    mcp_metadata_rejected=True,
                ),
            ),
            (
                {"mcp": {"server": "band", "tool": "known"}},
                {"arguments": {"x": 1}},
                ACPToolCall(
                    "call",
                    "band-known",
                    {"x": 1},
                    mcp_server="band",
                    mcp_tool="known",
                    title="native",
                ),
            ),
            (
                {"mcp": {"server": "band", "tool": "known"}},
                {},
                ACPToolCall(
                    "call", "native", {}, title="native", mcp_metadata_rejected=True
                ),
            ),
            ({}, {}, ACPToolCall("call", "native", {}, title="native")),
        ],
    )
    def test_metadata_rejection_is_distinct_from_fallback_identity(
        self,
        meta: dict[str, Any] | None,
        raw: dict[str, Any],
        expected: ACPToolCall,
    ) -> None:
        call = ACPToolCall.from_acp(
            ToolCallUpdate(
                tool_call_id="call", title="native", raw_input=raw, field_meta=meta
            )
        )

        assert call == expected

    async def test_a_permission_observer_does_not_replace_the_raw_handler_argument(
        self,
    ) -> None:
        observed: list[ACPToolCall] = []
        received: list[object] = []

        async def handle(**kwargs: object) -> dict[str, object]:
            received.append(kwargs["tool_call"])
            return cancel_permission()

        client = ACPCollectingClient(observe_tool_call=observed.append)
        client.set_permission_handler("session", handle)
        raw = ToolCallUpdate(tool_call_id="call", title="native")

        await client.request_permission([], "session", raw)

        assert received == [raw]
        assert observed == [ACPToolCall("call", "native", {}, title="native")]

    async def test_session_call_narration_notifies_the_same_observer(self) -> None:
        observed: list[ACPToolCall] = []
        client = ACPCollectingClient(observe_tool_call=observed.append)

        await client.session_update("session", start_tool_call("call", "native"))

        assert observed == [ACPToolCall("call", "native", {}, title="native")]
