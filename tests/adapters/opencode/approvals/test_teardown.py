"""Abandoning a turn or cleaning up a room releases every pending ask."""

from __future__ import annotations

import asyncio

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.integrations.opencode.types import (
    OpencodeSessionState,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.approvals.kit import (
    BlockingReplyClient,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    declined_questions,
    event_permission,
    event_question,
    make_platform_message,
    tools_protocol,
)


class TestTeardown:
    async def test_abandon_aborts_only_while_an_ask_is_still_parked_on_a_human(
        self,
        asks: AskFactory,
    ) -> None:
        """An ask the human already claimed isn't parked, so abandon leaves that
        session alone -- unless a sibling ask is still waiting on the room."""
        aborted: list[bool] = []

        async def record_abort() -> None:
            aborted.append(True)

        client = BlockingReplyClient("permission")
        approvals = make_room_approvals(
            client,
            abort_session=record_abort,
        )
        await approvals.on_permission_asked(asks.permission("req-a"))
        async with client.held(
            "permission", approvals.try_handle_reply("approve req-a", "user-1")
        ):
            assert await approvals.abandon() is False
            await approvals.on_permission_asked(
                asks.permission("req-b", permission="edit")
            )
            assert await approvals.abandon() is True

        assert aborted == [True]
        assert [reply["permission_id"] for reply in client.permission_replies] == [
            "req-a"
        ]

    async def test_cleanup_with_pending_permission(self) -> None:
        """Cleanup mid-permission cancels timeout without crash."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "perm-cleanup")]],
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        task = asyncio.create_task(
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

        await tools.until(
            lambda: any(
                "approval requested" in m["content"].lower()
                for m in tools.messages_sent
            )
        )

        # Cleanup while permission is pending
        await adapter.on_cleanup("room-1")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No permission reply should have been sent (just cleaned up), but the
        # abandoned session must still be told to stop working.
        assert fake_client.permission_replies == []
        assert fake_client.aborted_sessions == ["sess-1"]

    async def test_cleanup_with_pending_question(self) -> None:
        """Cleanup mid-question cancels timeout without crash."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_question("sess-1", "q-cleanup", "Something?")]
            ],
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(question_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        task = asyncio.create_task(
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

        await tools.until(
            lambda: any(
                "asked question" in m["content"].lower() for m in tools.messages_sent
            )
        )

        # Cleanup while question is pending
        await adapter.on_cleanup("room-1")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No question reply should have been sent, but the abandoned session
        # must still be told to stop working.
        assert fake_client.question_replies == []
        assert declined_questions(fake_client) == []
        assert fake_client.aborted_sessions == ["sess-1"]
