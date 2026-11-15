"""An ask nobody answers expires into its timeout reply exactly once."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.adapters.opencode.approvals.ports import ApprovalPorts
from band.adapters.opencode.approvals.room import RoomApprovals
from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import (
    OpencodeClientProtocol,
)
from band.integrations.opencode.types import (
    OpencodeSessionState,
)
from band.testing import FakeAgentTools, events_of_type
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.approvals.kit import (
    DECLINED,
    EXPIRY_CASES,
    BlockingReplyClient,
    ExpiryCase,
    approval_phases,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    AskFactory,
    FakeOpencodeClient,
    declined_questions,
    event_permission,
    event_question,
    event_session_idle,
    make_platform_message,
    time_passes,
    tools_protocol,
)


class TestExpiry:
    @pytest.mark.looptime
    async def test_a_redelivered_ask_waits_out_its_own_deadline(
        self, asks: AskFactory
    ) -> None:
        """An ask redelivered with the same id restarts its clock: the first
        delivery's deadline passes without expiring the replacement, which the
        room can still answer."""
        client = FakeOpencodeClient()
        approvals = make_room_approvals(
            client,
            config=OpencodeAdapterConfig(
                approval_wait_timeout_s=ASK_DEADLINE_S,
                approval_timeout_reply="reject",
                question_wait_timeout_s=ASK_DEADLINE_S,
            ),
        )

        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_question_asked(asks.question("q-1"))
        await time_passes(ASK_DEADLINE_S * 2 / 3)
        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_question_asked(asks.question("q-1"))
        await time_passes(ASK_DEADLINE_S * 2 / 3)

        assert client.permission_replies == [] and declined_questions(client) == []
        assert await approvals.try_handle_reply("approve req-1", "user-1")
        assert await approvals.try_handle_reply("Alice", "user-1")
        assert [reply["reply"] for reply in client.permission_replies] == ["once"]
        assert client.question_replies == [
            {"request_id": "q-1", "answers": [["Alice"]]}
        ]

    @pytest.mark.looptime
    async def test_a_turn_nobody_answers_expires_into_its_timeout_replies(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A turn's permission and question go unanswered: at their deadlines the
        permission is rejected and the question dismissed, OpenCode finishes the
        turn, and the room is told as procedural notices, not agent failures. Each
        phase is logged without the ask's command or question text."""
        approvals_logger = RoomApprovals.__module__
        caplog.set_level(logging.INFO, logger=approvals_logger)
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_permission("sess-1", "perm-1"),
                    event_question("sess-1", "q-1", "Pick a color"),
                ]
            ],
            reply_question_events={"q-1": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(
                approval_mode="manual",
                approval_wait_timeout_s=ASK_DEADLINE_S,
                approval_timeout_reply="reject",
                question_mode="manual",
                question_wait_timeout_s=ASK_DEADLINE_S * 2,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await tools.until(lambda: len(events_of_type(tools, "error")) == 2)

        assert fake_client.permission_replies == [
            {**DECLINED, "permission_id": "perm-1"}
        ]
        assert declined_questions(fake_client) == ["q-1"]
        notices = events_of_type(tools, "error")
        assert all("timed out" in notice["content"].lower() for notice in notices)
        assert all("failure" not in notice["metadata"] for notice in notices)
        assert approval_phases(caplog, approvals_logger) == [
            "OpenCode permission asked",
            "OpenCode question asked",
            "OpenCode permission expired",
            "OpenCode permission resolved",
            "OpenCode question expired",
            "OpenCode question rejected",
        ]
        assert "rm -rf tmp" not in caplog.text
        assert "Pick a color" not in caplog.text

        await adapter.on_cleanup("room-1")

    @pytest.mark.looptime
    @pytest.mark.parametrize("case", EXPIRY_CASES.values(), ids=EXPIRY_CASES.keys())
    async def test_an_expiry_owns_the_ask_it_claimed(
        self, case: ExpiryCase, asks: AskFactory
    ) -> None:
        """While an expired ask's reply is still in flight to OpenCode, the turn
        stays parked, a redelivery of that id is refused, and abandoning the turn
        doesn't cancel the reply; it lands exactly once, with its notice."""
        client = BlockingReplyClient(case.operation)
        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        room = make_room_approvals(
            client,
            tools=tools,
            config=OpencodeAdapterConfig(
                approval_wait_timeout_s=ASK_DEADLINE_S,
                approval_timeout_reply="reject",
                question_wait_timeout_s=ASK_DEADLINE_S,
            ),
        )

        await case.raise_ask(room, asks)
        async with client.held(case.operation):
            assert room.awaiting_human()
            await case.raise_ask(room, asks)
            await room.abandon()
        await room.wait_until_idle()
        await tools.until(lambda: bool(tools.events_sent))

        assert case.sent(client) == [case.expired_reply]
        assert "timed out" in tools.events_sent[0]["content"].lower()
        assert not room.awaiting_human()

    async def test_abandoning_a_request_stops_its_expiry_timer(
        self, asks: AskFactory
    ) -> None:
        """``_fail_request`` must cancel the timer it pops, or nothing ever can.

        ``cancel()`` reaches only entries still in the registry, so a timer left
        running past its entry's removal keeps the room's state alive — through the
        ``ApprovalPorts`` closures — until the full wait timeout elapses.
        """
        client: dict[str, OpencodeClientProtocol | None] = {
            "current": cast(OpencodeClientProtocol, FakeOpencodeClient())
        }
        aborted: list[bool] = []

        async def record_abort() -> None:
            aborted.append(True)

        approvals = RoomApprovals(
            OpencodeAdapterConfig(approval_mode="manual"),
            ApprovalPorts(
                room_id="room-1",
                client=lambda: client["current"],
                tools=lambda: cast(
                    AgentToolsProtocol, FakeAgentTools(participants=ROOM_MEMBERS)
                ),
                turn_mentions=list,
                release_turn_wait=lambda: None,
                fail_turn=lambda _message: None,
                abort_session=record_abort,
                is_own_band_tool=lambda _permission: False,
            ),
        )

        await approvals.on_permission_asked(asks.permission("perm-1"))
        timer = approvals._asks.permissions._timeouts["perm-1"]
        assert not timer.done()

        # The client is gone by the time the human replies — a teardown or a serve
        # restart mid-approval, which sends the reply down the abandonment path.
        client["current"] = None
        await approvals.try_handle_reply("approve perm-1", "user-1")

        assert aborted == [True]
        approvals.cancel()
        await asyncio.wait([timer])
