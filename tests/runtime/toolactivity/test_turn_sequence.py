from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import (
    SHOWN_MEMORY,
    SHOWN_REASONING,
    ActivityRecorder,
    shown_tool,
)
from tests.runtime.helpers import ROOM_ID

USER_ONE = {"id": "user-1", "name": "User One", "type": "User", "handle": "user-one"}
MEMORY = {
    "content": "User One prefers short answers",
    "system": "long_term",
    "type": "semantic",
    "segment": "user",
    "thought": "shapes later replies",
    "scope": "organization",
}


class TestTurnSequenceActivity:
    async def test_a_turn_shows_each_tool_in_the_order_the_model_called_it(
        self, recorder: ActivityRecorder, mock_rest_client: MagicMock
    ) -> None:
        memories = mock_rest_client.agent_api_memories
        memories.create_agent_memory.return_value = SimpleNamespace(
            data={"id": "memory-1"}
        )
        tools = recorder.observing(
            AgentTools(ROOM_ID, mock_rest_client, participants=[USER_ONE])
        )

        calls = [
            (BandTool.LOOKUP_PEERS, {}),
            (BandTool.SEND_MESSAGE, {"content": "Found one", "mentions": ["user-one"]}),
            (BandTool.STORE_MEMORY, MEMORY),
        ]
        for name, arguments in calls:
            outcome = await tools.execute_tool_call_structured(name, arguments)
            assert outcome.ok, outcome.error_message

        assert recorder.events == [
            shown_tool(BandTool.LOOKUP_PEERS),
            SHOWN_REASONING,
            shown_tool(BandTool.SEND_MESSAGE),
            SHOWN_REASONING,
            SHOWN_MEMORY,
            SHOWN_REASONING,
        ]
