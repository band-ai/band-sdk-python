from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from band_rest.errors import ForbiddenError
from band_rest.types import Error, ErrorError
from band_sdk_core import AgentFailure

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.tools import AgentTools


class TestStoppedRefusal:
    @pytest.mark.parametrize("boundary", ["dispatcher", "failure"])
    async def test_unbound_stopped_refusal_escapes_tool_error_conversion(
        self, mock_rest_client: MagicMock, boundary: str
    ) -> None:
        tools = AgentTools("room-123", mock_rest_client)
        mock_rest_client.agent_api_events.create_agent_chat_event.side_effect = (
            ForbiddenError(
                body=Error(
                    error=ErrorError(
                        code="forbidden",
                        message="Agent execution is stopped; cannot post events",
                        request_id="req-1",
                    )
                )
            )
        )
        with pytest.raises(RoomExecutionStoppedError):
            if boundary == "dispatcher":
                await tools.execute_tool_call_structured(
                    "band_send_event", {"content": "thought", "message_type": "thought"}
                )
            else:
                await tools.send_failure(AgentFailure("provider", "failed"))
        assert not tools.turn.complete
