"""A Copilot sub-agent's added participant is mentionable by the whole turn."""

from __future__ import annotations

import json
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
#: The agent the sub-agent adds (the default peer of ``mock_rest_client``), as
#: a sent message names it.
AGENT_TWO = ChatMessageRequestMentionsItem(handle="agent-two", id="agent-2")
ADD_AGENT_TWO = ToolCall(BandTool.ADD_PARTICIPANT, {"identifier": AGENT_TWO.handle})
WELCOME = ToolCall(
    BandTool.SEND_MESSAGE, {"content": "welcome", "mentions": [f"@{AGENT_TWO.handle}"]}
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


def succeeded(value: dict[str, str]) -> ToolResult:
    """The result the model receives for a tool call that returned ``value``."""
    return ToolResult(text_result_for_llm=json.dumps(value), result_type="success")


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
        added = {
            "id": AGENT_TWO.id,
            "name": "Agent Two",
            "role": "member",
            "status": "added",
        }
        posted = {"id": "msg-123", "content": "Hello", "sender_id": "agent-1"}
        assert results == [succeeded(added), succeeded(posted), succeeded(posted)]
        assert [call.kwargs["message"].mentions for call in sent.call_args_list] == [
            [AGENT_TWO],
            [AGENT_TWO],
        ]
