"""A turn released early to wait on a human is judged at its real end.

``on_event`` returns as soon as a permission ask parks the turn, so the
delivery is PROCESSED then; the turn's own outcome is judged when OpenCode
finishes it.
"""

from __future__ import annotations

import pytest

from band.core.exceptions import InputNotAccepted
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_event
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    agent_input,
    approve,
    event_message_updated,
    event_question,
    event_session_idle,
    event_text_part,
    park_turn,
    parked_turn_client,
    room_turn,
    turn_finished,
)


class TestDetachedTurns:
    async def test_a_detached_turn_that_ends_with_nothing_is_reported_once(
        self, make_adapter, tools
    ) -> None:
        adapter = make_adapter(
            parked_turn_client(after_approval=[event_session_idle("sess-1")])
        )

        await park_turn(adapter, tools)
        turn = await room_turn(adapter)
        assert failure_reports(tools) == []

        await approve(adapter)
        await turn_finished(turn)
        await adapter.on_cleanup("room-1")

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    async def test_a_busy_message_during_a_detached_turn_stays_unaccepted(
        self, make_adapter, tools, mcp_backend: BandMCPCalls
    ) -> None:
        """Unaccepted work leaves the detached turn's tool ownership intact."""
        adapter = make_adapter(
            parked_turn_client(
                after_approval=[
                    mcp_backend.band_tool_call(
                        BandTool.SEND_MESSAGE,
                        {"content": "Done.", "mentions": ["@alice"]},
                    ),
                    event_session_idle("sess-1"),
                ]
            )
        )
        busy_tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await park_turn(adapter, tools)
        turn = await room_turn(adapter)
        with pytest.raises(InputNotAccepted):
            await deliver_event(adapter, agent_input("are you done?", busy_tools))
        await approve(adapter)
        await turn_finished(turn)
        await adapter.on_cleanup("room-1")

        assert not busy_tools.turn.complete
        assert failure_reports(busy_tools) == []
        assert busy_tools.messages_sent == []
        assert tools.messages_sent[-1]["content"] == "Done."
        assert failure_reports(tools) == []

    async def test_a_detached_turn_cancelled_by_cleanup_posts_nothing(
        self, make_adapter, tools
    ) -> None:
        adapter = make_adapter(parked_turn_client(after_approval=[]))

        await park_turn(adapter, tools)
        await adapter.on_cleanup("room-1")

        assert failure_reports(tools) == []

    async def test_model_delivery_does_not_reclassify_a_newly_pending_question(
        self, make_adapter, tools
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[[event_question("sess-1", "q-1", "Which word?")]]
        )
        adapter = make_adapter(client)
        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_event(adapter, agent_input("ask me which word", tools))
        incoming = agent_input(
            "new ordinary work", FakeAgentTools(participants=ROOM_MEMBERS)
        )

        with pytest.raises(InputNotAccepted):
            await adapter.on_message(
                incoming.msg,
                incoming.tools,
                OpencodeSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id=incoming.room_id,
            )

        assert client.question_replies == []
        assert not incoming.tools.turn.complete
        await adapter.on_cleanup("room-1")

    async def test_a_rejected_question_hands_the_turn_back_to_the_model(
        self, make_adapter, tools
    ) -> None:
        """OpenCode ends a turn on its question-reject route, so the room's
        `reject` goes back as a declining answer and the model's reply then
        completes the parked turn."""
        adapter = make_adapter(
            FakeOpencodeClient(
                prompt_event_sequences=[
                    [event_question("sess-1", "q-1", "Which word?")]
                ],
                reply_question_events={
                    "q-1": [
                        event_message_updated("sess-1", "msg-1"),
                        event_text_part("sess-1", "msg-1", "OK, I'll go without one."),
                        event_session_idle("sess-1"),
                    ]
                },
            )
        )
        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_event(adapter, agent_input("ask me which word", tools))
        turn = await room_turn(adapter)

        await deliver_event(
            adapter, agent_input("reject", FakeAgentTools(participants=ROOM_MEMBERS))
        )
        await turn_finished(turn)
        await adapter.on_cleanup("room-1")

        assert tools.messages_sent[-1]["content"] == "OK, I'll go without one."
        assert failure_reports(tools) == []
