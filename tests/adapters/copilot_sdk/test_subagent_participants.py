"""A Copilot sub-agent's added participant is mentionable by the whole turn."""

from __future__ import annotations

from unittest.mock import MagicMock

from band_rest.types import ChatMessageRequestMentionsItem

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.client.rest import ChatParticipant
from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    call_tool,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)
from tests.framework_conformance.turnprobes import ToolCall

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolResult

SUBAGENT = "subagent-1"
ADD_AGENT_TWO = ToolCall(BandTool.ADD_PARTICIPANT, {"identifier": "agent-two"})
WELCOME = ToolCall(
    BandTool.SEND_MESSAGE, {"content": "welcome", "mentions": ["@agent-two"]}
)


#: The room's one member before the sub-agent adds anyone.
ROOM_USER = ChatParticipant(
    id="user-1",
    name="User One",
    type="User",
    handle="user-one",
    role="member",
    status="active",
)
#: How a sent message names the added agent (the default peer of
#: ``mock_rest_client``).
AGENT_TWO_MENTION = ChatMessageRequestMentionsItem(handle="agent-two", id="agent-2")


class TestSubagentParticipants:
    async def test_root_and_sub_agent_mention_whom_the_sub_agent_added(
        self, mock_rest_client: MagicMock
    ) -> None:
        results: list[ToolResult] = []

        async def sub_agent_adds_then_both_mention(
            session: FakeCopilotSession,
        ) -> None:
            results.append(await call_tool(session, ADD_AGENT_TWO, agent_id=SUBAGENT))
            results.append(await call_tool(session, WELCOME))
            results.append(await call_tool(session, WELCOME, agent_id=SUBAGENT))

        participants = mock_rest_client.agent_api_participants
        participants.list_agent_chat_participants.return_value = MagicMock(
            data=[ROOM_USER]
        )
        client = FakeCopilotClient(turn_events=[sub_agent_adds_then_both_mention])
        adapter = await make_started_adapter(client)
        tools = AgentTools("room-1", mock_rest_client, participants=[], agent_id="me")

        await run_event(adapter, tools)

        sent = mock_rest_client.agent_api_messages.create_agent_chat_message
        assert [result.result_type for result in results] == ["success"] * 3
        assert [call.kwargs["message"].mentions for call in sent.call_args_list] == [
            [AGENT_TWO_MENTION],
            [AGENT_TWO_MENTION],
        ]
