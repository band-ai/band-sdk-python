from __future__ import annotations

import asyncio

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.integrations.opencode import ApprovalReply, parse_opencode_event
from band.integrations.opencode.types import OpencodeSessionState
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_permission,
    event_session_idle,
    make_platform_message,
    room_turn,
    run_single_turn,
    tools_protocol,
    turn_finished,
    wait_for,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

DELIBERATION_S = 4.0


class TestApprovalLifetime:
    async def test_session_idle_does_not_abort_an_in_flight_approval(
        self, make_adapter, tools
    ) -> None:
        """session.idle during an already-claimed human reply must finish the
        turn without aborting the session the human just answered."""

        class BlockBeforeReplyClient(FakeOpencodeClient):
            def __init__(self) -> None:
                super().__init__(prompt_event_sequences=[[]])
                self.reply_started = asyncio.Event()
                self.allow_reply = asyncio.Event()

            async def reply_permission(
                self,
                permission_id: str,
                *,
                reply: ApprovalReply,
                message: str | None = None,
            ) -> None:
                self.reply_started.set()
                await self.allow_reply.wait()
                await super().reply_permission(
                    permission_id, reply=reply, message=message
                )

        fake_client = BlockBeforeReplyClient()
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        turn_task = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await wait_for(lambda: bool(fake_client.prompt_calls))
        await adapter._handle_event(
            parse_opencode_event(event_permission("sess-1", "perm-1"))
        )
        await turn_task

        reply_task = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(content="approve perm-1"),
                tools_protocol(tools),
                OpencodeSessionState(session_id="sess-1", room_id="room-1"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )
        )
        await asyncio.wait_for(fake_client.reply_started.wait(), timeout=1.0)
        await adapter._handle_event(parse_opencode_event(event_session_idle("sess-1")))
        assert fake_client.aborted_sessions == []
        fake_client.allow_reply.set()
        await reply_task
        assert fake_client.permission_replies == [
            {"permission_id": "perm-1", "reply": "once"}
        ]
        await adapter.on_cleanup("room-1")

    async def test_stale_approval_id_survives_a_later_turn(
        self, make_adapter, tools
    ) -> None:
        """Room-scoped known ids must outlive `_begin_turn`'s RoomApprovals
        rebuild so `approve <old-id>` is feedback, not a new prompt."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-1")],
                [event_session_idle("sess-1")],
            ],
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="run it"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await deliver_message(
            adapter,
            make_platform_message(content="approve perm-1"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        first_turn = await room_turn(adapter)
        await adapter._handle_event(parse_opencode_event(event_session_idle("sess-1")))
        await turn_finished(first_turn)

        await deliver_message(
            adapter,
            make_platform_message(content="next turn"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await wait_for(lambda: len(fake_client.prompt_calls) == 2)

        await deliver_message(
            adapter,
            make_platform_message(content="approve perm-1"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(fake_client.prompt_calls) == 2
        assert any(
            "no longer pending" in message["content"] for message in tools.messages_sent
        )
        await adapter.on_cleanup("room-1")

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_new_turn_does_not_inherit_previous_human_wait(
        self, make_adapter, tools
    ) -> None:
        """`_begin_turn` rebuilds RoomApprovals so banked deliberation from
        turn N is not added to turn N+1's compute deadline."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-1")],
                [event_session_idle("sess-1")],
            ],
        )
        adapter = make_adapter(
            fake_client,
            config=OpencodeAdapterConfig(approval_mode="manual", turn_timeout_s=30.0),
        )

        async with controlled_clock() as clock:
            await run_single_turn(adapter, tools, content="run it")
            room_state = adapter._rooms["room-1"]
            assert room_state.turn is not None and room_state.turn.watcher is not None
            watcher = room_state.turn.watcher
            await clock.elapse(DELIBERATION_S)
            await deliver_message(
                adapter,
                make_platform_message(content="approve perm-1"),
                tools_protocol(tools),
                OpencodeSessionState(session_id="sess-1", room_id="room-1"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )
            await adapter._handle_event(
                parse_opencode_event(event_session_idle("sess-1"))
            )
            await watcher
        assert room_state.approvals.human_wait_seconds == DELIBERATION_S

        await deliver_message(
            adapter,
            make_platform_message(content="next turn"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await wait_for(lambda: len(fake_client.prompt_calls) == 2)

        assert room_state.approvals.human_wait_seconds == 0
        await adapter.on_cleanup("room-1")
