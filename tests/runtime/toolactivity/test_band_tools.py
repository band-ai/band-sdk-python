from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import BandTool, Surface
from band.testing.activity import ActivityRecorder
from tests.runtime.helpers import ROOM_ID

USER_ONE = {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
EMPTY_PAGE = {"data": []}


class TestBandToolActivity:
    @pytest.mark.parametrize(
        ("name", "arguments"),
        [
            (BandTool.GET_PARTICIPANTS, {}),
            (BandTool.SEND_MESSAGE, {"content": "hi", "mentions": ["user-one"]}),
            (BandTool.LIST_CONTACTS, {}),
            (BandTool.LIST_ROOM_FILES, {}),
            (BandTool.LIST_TASKS, {}),
        ],
        ids=["participants", "chat", "contacts", "files", "tasks"],
    )
    async def test_a_band_tool_shows_its_name_until_it_ends(
        self,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
        name: BandTool,
        arguments: dict[str, Any],
    ) -> None:
        mock_rest_client.agent_api_contacts.list_agent_contacts.return_value = (
            EMPTY_PAGE
        )
        mock_rest_client.agent_api_chat_tasks.list_chat_tasks.return_value = EMPTY_PAGE
        tools = recorder.observing(
            AgentTools(ROOM_ID, mock_rest_client, participants=[USER_ONE])
        )
        outcome = await tools.execute_tool_call_structured(name, arguments)
        assert outcome.ok, outcome.error_message
        recorder.assert_completed_tool(name)

    def test_every_agent_tool_is_defined_where_its_decorators_wrap_it(self) -> None:
        # The class decorators wrap only methods AgentTools itself defines; one
        # inherited from a base class would run unreported.
        inherited = {
            definition.name
            for definition in TOOL_DEFINITIONS.values()
            if definition.surface == Surface.AGENT
            and definition.method_name not in vars(AgentTools)
        }
        assert inherited == set()

    async def test_a_failing_band_tool_still_returns_to_reasoning(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
    ) -> None:
        participants = mock_rest_client.agent_api_participants
        participants.list_agent_chat_participants.side_effect = RuntimeError("down")
        with pytest.raises(RuntimeError):
            await tools.get_participants()
        recorder.assert_completed_tool(BandTool.GET_PARTICIPANTS)

    async def test_adapter_telemetry_is_quiet(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        await tools.send_event("private telemetry", "thought")
        assert recorder.events == []

    async def test_a_model_sent_event_shows_as_a_tool_call(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        await tools.execute_tool_call(
            BandTool.SEND_EVENT,
            {"content": "model telemetry", "message_type": "thought"},
        )
        recorder.assert_completed_tool(BandTool.SEND_EVENT)
