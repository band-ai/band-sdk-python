from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session

from band.runtime.tools import AgentTools
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder
from tests.runtime.helpers import ROOM_ID
from tests.runtime.toolactivity.kit import band_tool_server


def cancel_request(request_id: Any) -> types.ClientNotification:
    return types.ClientNotification(
        types.CancelledNotification(
            params=types.CancelledNotificationParams(requestId=request_id)
        )
    )


class TestCancelledToolActivity:
    async def test_a_cancelled_mcp_tool_call_returns_to_reasoning(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
    ) -> None:
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def held(*_args: Any, **_kwargs: Any) -> Any:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        participants = mock_rest_client.agent_api_participants
        participants.list_agent_chat_participants.side_effect = held
        request_ids: list[Any] = []
        server = band_tool_server(
            tools, TOOL_DEFINITIONS[BandTool.GET_PARTICIPANTS], request_ids=request_ids
        )
        async with create_connected_server_and_client_session(server) as session:
            request = asyncio.create_task(
                session.call_tool(BandTool.GET_PARTICIPANTS, {"chat_id": ROOM_ID})
            )
            try:
                await entered.wait()
                await session.send_notification(cancel_request(request_ids[-1]))
                await cancelled.wait()
            finally:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)

        recorder.assert_completed_tool(BandTool.GET_PARTICIPANTS)
