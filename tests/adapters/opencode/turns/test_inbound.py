from __future__ import annotations

import asyncio

import pytest

from band.adapters.opencode import OpencodeAdapter
from band.core.exceptions import InputNotAccepted
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    HeldReplyTools,
    approve,
    deliver_followup,
    event_message_updated,
    event_permission,
    event_session_idle,
    model_answer,
    park_turn,
    parked_turn_client,
    run_single_turn,
    wait_for,
)


def assert_left_untouched(tools: FakeAgentTools) -> None:
    """A refused delivery neither ran nor judged a turn of its own."""
    assert not tools.turn.complete
    assert tools.events_sent == []
    assert tools.messages_sent == []


async def room_turn_finished(adapter: OpencodeAdapter) -> None:
    room_state = await adapter._get_or_create_room_state("room-1")
    turn = room_state.turn
    assert turn is not None
    await wait_for(lambda: not turn.running)


class TestInbound:
    async def test_approval_reply_is_settled_and_leaves_the_turn_its_tools(
        self, make_adapter, tools, mcp_backend: BandMCPCalls
    ) -> None:
        """The approve message runs no model, so its own turn is settled; and the
        resumed turn's band tool calls still resolve to the turn's tools."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "req-1")]],
            reply_permission_events={
                "req-1": [
                    mcp_backend.band_tool_call(
                        BandTool.SEND_MESSAGE,
                        {"content": "Done.", "mentions": ["@alice"]},
                    ),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = make_adapter(fake_client)
        approver_tools = FakeAgentTools()

        await run_single_turn(adapter, tools, content="run it")
        await deliver_followup(adapter, approver_tools, content="approve req-1")
        await wait_for(lambda: tools.turn.replied)

        assert approver_tools.turn.complete
        assert approver_tools.messages_sent == []
        assert tools.messages_sent[-1]["content"] == "Done."

    async def test_busy_message_is_unaccepted_and_leaves_the_turn_its_tools(
        self, make_adapter, tools, mcp_backend: BandMCPCalls
    ) -> None:
        """Unaccepted work cannot rebind the running turn's tools."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_message_updated("sess-1", "msg-1")]]
        )
        adapter = make_adapter(fake_client)
        busy_tools = FakeAgentTools()
        running_turn = asyncio.create_task(run_single_turn(adapter, tools))
        await wait_for(lambda: len(fake_client.prompt_calls) == 1)

        with pytest.raises(InputNotAccepted):
            await deliver_followup(adapter, busy_tools, content="are you done?")
        await fake_client.push_event(
            mcp_backend.band_tool_call(
                BandTool.SEND_MESSAGE, {"content": "Done.", "mentions": ["@alice"]}
            )
        )
        await fake_client.push_event(event_session_idle("sess-1"))
        await running_turn

        assert_left_untouched(busy_tools)
        assert tools.chat == ["Done."]

    async def test_a_message_during_the_previous_reply_waits_for_that_turn(
        self, make_adapter
    ) -> None:
        """The previous turn still owns the room until its watcher has posted
        the reply; a message arriving then is busy, and admitted after."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                model_answer("msg-1", "first answer"),
                model_answer("msg-2", "second answer"),
            ]
        )
        adapter = make_adapter(fake_client)
        first_tools, busy_tools, next_tools = (
            HeldReplyTools(),
            FakeAgentTools(),
            FakeAgentTools(),
        )
        running_turn = asyncio.create_task(run_single_turn(adapter, first_tools))
        await first_tools.reply_entered.wait()

        with pytest.raises(InputNotAccepted):
            await deliver_followup(adapter, busy_tools, content="and then?")
        assert len(fake_client.prompt_calls) == 1
        assert_left_untouched(busy_tools)

        first_tools.reply_release.set()
        await running_turn
        assert first_tools.turn.complete
        await deliver_followup(adapter, next_tools, content="and then?")

        assert first_tools.chat == ["first answer"]
        assert next_tools.chat == ["second answer"]

    async def test_a_message_during_an_approved_turns_reply_waits_for_that_turn(
        self, make_adapter
    ) -> None:
        """A turn released to wait on a person keeps the room through the reply
        it posts after the approval."""
        fake_client = parked_turn_client(
            after_approval=model_answer("msg-1", "first answer"),
            later_prompts=[model_answer("msg-2", "second answer")],
        )
        adapter = make_adapter(fake_client)
        first_tools, busy_tools, next_tools = (
            HeldReplyTools(),
            FakeAgentTools(),
            FakeAgentTools(),
        )
        await park_turn(adapter, first_tools)
        await approve(adapter)
        await first_tools.reply_entered.wait()

        with pytest.raises(InputNotAccepted):
            await deliver_followup(adapter, busy_tools, content="and then?")
        assert len(fake_client.prompt_calls) == 1
        assert_left_untouched(busy_tools)

        first_tools.reply_release.set()
        await room_turn_finished(adapter)
        assert first_tools.turn.complete
        await deliver_followup(adapter, next_tools, content="and then?")
        await adapter.on_cleanup("room-1")

        assert first_tools.chat[-1] == "first answer"
        assert next_tools.chat == ["second answer"]
