"""TurnLedger behavior at the CrewAI tool boundary."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandToolError
from band.runtime.custom_tools import get_custom_tool_name
from band.runtime.tools import (
    BandTool,
)
from tests.integrations.crewaitools.kit import (
    RecordInput,
    record_value,
    record_value_for_real,
)


class TestTurnLedger:
    @pytest.mark.parametrize(
        ("tool", "arguments", "replied", "complete"),
        [
            (
                BandTool.SEND_MESSAGE,
                {"content": "hi", "mentions": ["@alice"]},
                True,
                True,
            ),
            (BandTool.NO_REPLY, {"reason": "FYI only"}, True, True),
            (BandTool.CREATE_CHATROOM, {}, False, True),
            (BandTool.LOOKUP_PEERS, {}, False, False),
            (
                BandTool.SEND_EVENT,
                {"content": "hm", "message_type": "thought"},
                False,
                False,
            ),
        ],
        ids=["reply", "decline", "act", "observe", "narrate"],
    )
    def test_a_tool_call_records_its_effect_on_the_turn(
        self, crew_tools, room_tools, tool, arguments, replied, complete
    ):
        crew_tools()[tool]._run(**arguments)

        assert (room_tools.turn.replied, room_tools.turn.complete) == (
            replied,
            complete,
        )

    def test_failed_send_then_event_leaves_the_turn_incomplete(
        self, crew_tools, room_tools
    ):
        """A rejected reply followed by narration is still a turn owed a reply."""
        room_tools.send_message_error = BandToolError("Unknown participant 'user1'")
        tools = crew_tools()

        tools[BandTool.SEND_MESSAGE]._run(content="hi", mentions=["@user1"])
        tools[BandTool.SEND_EVENT]._run(content="thinking", message_type="thought")

        assert (room_tools.messages_sent, len(room_tools.events_sent)) == ([], 1)
        assert not room_tools.turn.complete

    def test_tracker_keeps_what_a_successful_send_said(self, crew_tools, tracker):
        crew_tools()[BandTool.SEND_MESSAGE]._run(content="hi", mentions=["@alice"])

        assert (tracker.posts, tracker.any_tool_ran) == (["(to @alice) hi"], True)

    def test_a_failed_send_still_counts_as_a_tool_that_ran(
        self, crew_tools, room_tools, tracker
    ):
        room_tools.send_message_error = BandToolError("boom")

        crew_tools()[BandTool.SEND_MESSAGE]._run(content="hi", mentions=["@alice"])

        assert (tracker.posts, tracker.any_tool_ran) == ([], True)

    @pytest.mark.parametrize(
        ("handler", "completes"),
        [(record_value, False), (record_value_for_real, True)],
        ids=["undeclared-observes", "declared-act"],
    )
    def test_custom_tool_records_its_declared_effect(
        self, crew_tools, room_tools, handler, completes
    ):
        tool_name = get_custom_tool_name(RecordInput)

        crew_tools([(RecordInput, handler)])[tool_name]._run(value="x")

        assert room_tools.turn.complete is completes
