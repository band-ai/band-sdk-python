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
        ("meta", "raw", "server", "arguments", "rejected"),
        [
            (
                None,
                {"server": "band", "tool": "known", "arguments": {"x": 1}},
                "band",
                {"x": 1},
                False,
            ),
            (
                {"mcp": None},
                {"server": "band", "tool": "known", "arguments": {"x": 1}},
                "band",
                {"x": 1},
                True,
            ),
            (
                {"mcp": {"server": "band", "tool": "known"}},
                {"arguments": {"x": 1}},
                "band",
                {"x": 1},
                False,
            ),
            ({"mcp": {"server": "band", "tool": "known"}}, {}, None, {}, True),
            ({}, {}, None, {}, False),
        ],
    )
    def test_metadata_rejection_is_distinct_from_fallback_identity(
        self,
        meta: dict[str, Any] | None,
        raw: dict[str, Any],
        server: str | None,
        arguments: dict[str, Any],
        rejected: bool,
    ) -> None:
        call = ACPToolCall.from_acp(
            ToolCallUpdate(
                tool_call_id="call", title="native", raw_input=raw, field_meta=meta
            )
        )

        assert (call.mcp_server, call.arguments, call.mcp_metadata_rejected) == (
            server,
            arguments,
            rejected,
        )

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
