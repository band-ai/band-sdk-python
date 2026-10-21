from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from band.core.exceptions import InputNotAccepted
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    HeldReplyTools,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
    tools_protocol,
    wait_for,
)


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
        await deliver_message(
            adapter,
            make_platform_message(content="approve req-1"),
            tools_protocol(approver_tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
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
            await deliver_message(
                adapter,
                make_platform_message(content="are you done?"),
                tools_protocol(busy_tools),
                OpencodeSessionState(session_id="sess-1", room_id="room-1"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )
        await fake_client.push_event(
            mcp_backend.band_tool_call(
                BandTool.SEND_MESSAGE, {"content": "Done.", "mentions": ["@alice"]}
            )
        )
        await fake_client.push_event(event_session_idle("sess-1"))
        await running_turn

        assert not busy_tools.turn.complete
        assert busy_tools.events_sent == []
        assert tools.chat == ["Done."]

    async def test_a_message_during_the_previous_reply_waits_for_that_turn(
        self, make_adapter
    ) -> None:
        """The previous turn still owns the room until its watcher has posted
        the reply; a message arriving then is busy, and admitted after."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "first answer"),
                    event_session_idle("sess-1"),
                ],
                [
                    event_message_updated("sess-1", "msg-2"),
                    event_text_part("sess-1", "msg-2", "second answer"),
                    event_session_idle("sess-1"),
                ],
            ]
        )
        adapter = make_adapter(fake_client)
        first_tools = HeldReplyTools()
        running_turn = asyncio.create_task(run_single_turn(adapter, first_tools))
        await first_tools.reply_entered.wait()

        def next_message(tools: FakeAgentTools) -> Awaitable[None]:
            return deliver_message(
                adapter,
                make_platform_message(content="and then?"),
                tools_protocol(tools),
                OpencodeSessionState(session_id="sess-1", room_id="room-1"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        with pytest.raises(InputNotAccepted):
            await next_message(FakeAgentTools())
        first_tools.reply_release.set()
        await running_turn
        next_tools = FakeAgentTools()
        await next_message(next_tools)

        assert first_tools.chat == ["first answer"]
        assert next_tools.chat == ["second answer"]
