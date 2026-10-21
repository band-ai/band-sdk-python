from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder
from tests.adapters.activity.kit import MODEL_CALLS, ModelCall
from tests.runtime.helpers import ROOM_ID

EVENT = {"content": "model telemetry", "message_type": "thought"}


class TestModelToolActivity:
    """A model's tool call shows exactly once, whichever framework dispatched it."""

    @pytest.mark.parametrize("call", MODEL_CALLS)
    @pytest.mark.parametrize(
        ("name", "arguments"),
        [(BandTool.SEND_EVENT, EVENT), (BandTool.GET_PARTICIPANTS, {})],
        ids=["send_event", "get_participants"],
    )
    async def test_the_call_shows_once_while_adapter_telemetry_stays_quiet(
        self,
        mock_rest_client: MagicMock,
        call: ModelCall,
        name: BandTool,
        arguments: dict[str, Any],
    ) -> None:
        recorder = ActivityRecorder()
        tools = recorder.observing(AgentTools(ROOM_ID, mock_rest_client))
        await tools.send_event("adapter telemetry", "thought")
        assert recorder.events == []

        await call(tools, name, arguments)

        recorder.assert_completed_tool(name)
