"""A manual permission reply in the room is routed to the pending OpenCode ask."""

from __future__ import annotations

import asyncio

from band.integrations.opencode.types import (
    OpencodeSessionState,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.approvals.kit import (
    ALICE_PARTICIPANT,
    DECLINED,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)
from tests.testing.support import seeded_participant


class TestPermissionReplies:
    async def test_manual_permission_reply_preserves_mixed_case_request_id(
        self,
        asks: AskFactory,
    ) -> None:
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)

        await approvals.on_permission_asked(asks.permission("Req-AbC-123"))

        assert await approvals.try_handle_reply("APPROVE Req-AbC-123", "user-1")
        assert client.permission_replies == [
            {
                "permission_id": "Req-AbC-123",
                "reply": "once",
            }
        ]

    async def test_mentioned_permission_reply_is_recognized(
        self, asks: AskFactory
    ) -> None:
        """A real reply reaches on_message with the platform's ``@handle`` block
        prepended (a room message is delivered only when it mentions the agent).
        The reply must still be recognized -- and the server's mixed-case id
        preserved -- not misread as a new prompt."""
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)

        await approvals.on_permission_asked(asks.permission("Req-AbC-123"))

        assert await approvals.try_handle_reply(
            "@alexander.zaikman/tom approve Req-AbC-123", "user-1"
        )
        assert client.permission_replies == [
            {
                "permission_id": "Req-AbC-123",
                "reply": "once",
            }
        ]

    async def test_concurrent_permission_asks_are_both_answerable(
        self, asks: AskFactory
    ) -> None:
        """OpenCode can have several asks outstanding (its own clients keep a
        per-session list). A second ask must not evict the first, whose tool call
        would then block server-side until the turn timed out."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools(participants=[ALICE_PARTICIPANT])
        approvals = make_room_approvals(client, tools=tools)

        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_permission_asked(asks.permission("req-2", permission="edit"))

        assert await approvals.try_handle_reply("approve req-2", "user-1")
        assert await approvals.try_handle_reply("reject req-1", "user-1")
        assert client.permission_replies == [
            {"permission_id": "req-2", "reply": "once"},
            {**DECLINED, "permission_id": "req-1"},
        ]
        # Both asks resolved, so the turn watcher is no longer parked on a human.
        assert not approvals.awaiting_human()

    async def test_unnamed_reply_asks_which_of_several_approvals(
        self, asks: AskFactory
    ) -> None:
        """A bare `approve` cannot pick between two pending asks. Naming them beats
        both guessing and forwarding the reply to the model as a fresh prompt."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools(participants=[ALICE_PARTICIPANT])
        approvals = make_room_approvals(client, tools=tools)

        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_permission_asked(asks.permission("req-2", permission="edit"))

        assert await approvals.try_handle_reply("approve", "user-1")
        assert client.permission_replies == []
        assert "`req-1`, `req-2`" in tools.messages_sent[-1]["content"]
        assert approvals.awaiting_human()

    async def test_one_resolved_ask_keeps_the_other_parked(
        self, asks: AskFactory
    ) -> None:
        """The watcher must stay parked while a second ask still owes a reply,
        otherwise its human-wait time is charged to the compute budget."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools(participants=[ALICE_PARTICIPANT])
        approvals = make_room_approvals(client, tools=tools)

        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_permission_asked(asks.permission("req-2", permission="edit"))

        assert await approvals.try_handle_reply("approve req-1", "user-1")
        assert approvals.awaiting_human()

    async def test_polite_permission_reply_uses_pending_request(
        self, asks: AskFactory
    ) -> None:
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)
        await approvals.on_permission_asked(asks.permission("req-1"))

        assert await approvals.try_handle_reply("approve please", "user-1")
        assert client.permission_replies[0]["permission_id"] == "req-1"

    async def test_reply_to_nonmatching_request_id_is_consumed(
        self, asks: AskFactory
    ) -> None:
        """A named stale approval command is feedback, not a new model prompt."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        approvals = make_room_approvals(client, tools=tools)

        await approvals.on_permission_asked(asks.permission("req-current"))

        assert await approvals.try_handle_reply("approve req-stale", "user-1")
        assert client.permission_replies == []
        assert "no longer pending" in tools.messages_sent[-1]["content"]

    async def test_reply_naming_a_never_asked_id_is_not_consumed(self) -> None:
        """A room message naming an id this room has never asked about is an
        ordinary message, not a reply -- it must be forwarded to the model
        rather than swallowed as 'no longer pending' feedback."""
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)

        assert not await approvals.try_handle_reply("approve never-asked-id", "user-1")
        assert client.permission_replies == []

    async def test_reply_naming_an_already_resolved_id_is_consumed(
        self,
        asks: AskFactory,
    ) -> None:
        """A reply that arrives after every pending ask has already resolved
        still gets 'no longer pending' feedback instead of being forwarded to
        the model as a fresh prompt."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools(
            participants=[seeded_participant("user-1", handle="@alice")]
        )
        approvals = make_room_approvals(client, tools=tools)

        await approvals.on_permission_asked(asks.permission("perm-1"))
        assert await approvals.try_handle_reply("approve perm-1", "user-1")
        client.permission_replies.clear()

        assert await approvals.try_handle_reply("approve perm-1", "user-1")
        assert client.permission_replies == []
        assert "no longer pending" in tools.messages_sent[-1]["content"]

    async def test_manual_permission_reply_from_follow_up_message(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "req-1")]],
            reply_permission_events={
                "req-1": [
                    event_message_updated("sess-1", "msg-3"),
                    event_text_part("sess-1", "msg-3", "Approved and done"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(content="Please continue"),
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
        await asyncio.wait([first_turn])
        assert all(msg["content"] != "Approved and done" for msg in tools.messages_sent)
        # Regression: FakeAgentTools records a call made with no mentions instead
        # of rejecting it like the real AgentTools.send_message does, so this must
        # be asserted explicitly -- it silently passed before mentions was wired.
        approval_requested = next(
            m
            for m in tools.messages_sent
            if "approval requested" in m["content"].lower()
        )
        assert approval_requested["mentions"]

        await deliver_message(
            adapter,
            make_platform_message(content="approve req-1"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await first_turn
        await tools.until(
            lambda: any(
                msg["content"] == "Approved and done" for msg in tools.messages_sent
            )
        )

        assert fake_client.permission_replies == [
            {"permission_id": "req-1", "reply": "once"}
        ]
        assert any(msg["content"] == "Approved and done" for msg in tools.messages_sent)
        handled_with = next(
            m for m in tools.messages_sent if "handled with" in m["content"].lower()
        )
        assert handled_with["mentions"]

    async def test_always_permission_reply_from_follow_up_message(
        self, make_adapter, tools
    ) -> None:
        """The `always <id>` reply maps to the `always` ApprovalReply (distinct
        from the one-shot `approve <id>` -> `once`)."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "req-always")]],
            reply_permission_events={
                "req-always": [
                    event_message_updated("sess-1", "msg-always"),
                    event_text_part("sess-1", "msg-always", "Always approved"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(content="Please continue"),
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
        await asyncio.wait([first_turn])

        await deliver_message(
            adapter,
            make_platform_message(content="always req-always"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await first_turn
        await tools.until(
            lambda: any(
                msg["content"] == "Always approved" for msg in tools.messages_sent
            )
        )

        assert fake_client.permission_replies == [
            {
                "permission_id": "req-always",
                "reply": "always",
            }
        ]
