"""Tests for OpencodeAdapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal, cast

from pydantic import BaseModel

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.adapters.opencode.approvals import ApprovalPorts, RoomApprovals
from band.core.protocols import AgentToolsProtocol
from band.core.types import Capability
from band.integrations.opencode import (
    OpencodeClientProtocol,
    OpencodePermissionRequest,
    OpencodeQuestionRequest,
)
from band.integrations.opencode.types import (
    OpencodeSessionState,
)
from band.testing import FakeAgentTools


from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    RaisingSendTools,
    run_single_turn,
    event_message_updated,
    event_permission,
    event_question,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
    wait_for,
)


class BlockingReplyClient(FakeOpencodeClient):
    """Pause one reply request to deterministically interleave a new ask."""

    def __init__(self, operation: Literal["permission", "question", "reject"]) -> None:
        super().__init__()
        self.operation = operation
        self.reply_started = asyncio.Event()
        self.allow_reply = asyncio.Event()

    async def _block(self, operation: str) -> None:
        if self.operation != operation:
            return
        self.reply_started.set()
        await self.allow_reply.wait()

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        *,
        response: str,
    ) -> None:
        await super().reply_permission(session_id, permission_id, response=response)
        await self._block("permission")

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None:
        await super().reply_question(request_id, answers=answers)
        await self._block("question")

    async def reject_question(self, request_id: str) -> None:
        await super().reject_question(request_id)
        await self._block("reject")


class FailingReplyClient(FakeOpencodeClient):
    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        *,
        response: str,
    ) -> None:
        raise RuntimeError("permission reply failed")

    async def reject_question(self, request_id: str) -> None:
        raise RuntimeError("question rejection failed")


def make_room_approvals(
    client: OpencodeClientProtocol,
    *,
    tools: FakeAgentTools | None = None,
    release_turn_wait: Callable[[], None] = lambda: None,
    fail_turn: Callable[[str], None] = lambda _message: None,
    config: OpencodeAdapterConfig | None = None,
) -> RoomApprovals:
    tools = tools if tools is not None else FakeAgentTools()
    return RoomApprovals(
        config or OpencodeAdapterConfig(),
        ApprovalPorts(
            room_id="room-1",
            session_id=lambda: "sess-1",
            client=lambda: client,
            tools=lambda: cast(AgentToolsProtocol, tools),
            turn_mentions=lambda: [],
            release_turn_wait=release_turn_wait,
            fail_turn=fail_turn,
            is_own_band_tool=lambda _permission: False,
        ),
    )


async def test_manual_permission_reply_preserves_mixed_case_request_id() -> None:
    client = FakeOpencodeClient()
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="Req-AbC-123", permission="bash")
    )

    assert await approvals.try_handle_reply("APPROVE Req-AbC-123", "user-1")
    assert client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "Req-AbC-123",
            "response": "once",
        }
    ]


async def test_mentioned_permission_reply_is_recognized() -> None:
    """A real reply reaches on_message with the platform's ``@handle`` block
    prepended (a room message is delivered only when it mentions the agent).
    The reply must still be recognized -- and the server's mixed-case id
    preserved -- not misread as a new prompt."""
    client = FakeOpencodeClient()
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="Req-AbC-123", permission="bash")
    )

    assert await approvals.try_handle_reply(
        "@alexander.zaikman/tom approve Req-AbC-123", "user-1"
    )
    assert client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "Req-AbC-123",
            "response": "once",
        }
    ]


async def test_concurrent_permission_asks_are_both_answerable() -> None:
    """OpenCode can have several asks outstanding (its own clients keep a
    per-session list). A second ask must not evict the first, whose tool call
    would then block server-side until the turn timed out."""
    client = FakeOpencodeClient()
    tools = FakeAgentTools(participants=[{"id": "user-1", "handle": "@alice"}])
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client), tools=tools)

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-1", permission="bash")
    )
    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-2", permission="edit")
    )

    assert await approvals.try_handle_reply("approve req-2", "user-1")
    assert await approvals.try_handle_reply("reject req-1", "user-1")
    assert [
        (reply["permission_id"], reply["response"])
        for reply in client.permission_replies
    ] == [("req-2", "once"), ("req-1", "reject")]
    # Both asks resolved, so the turn watcher is no longer parked on a human.
    assert not approvals.awaiting_human()


async def test_unnamed_reply_asks_which_of_several_approvals() -> None:
    """A bare `approve` cannot pick between two pending asks. Naming them beats
    both guessing and forwarding the reply to the model as a fresh prompt."""
    client = FakeOpencodeClient()
    tools = FakeAgentTools(participants=[{"id": "user-1", "handle": "@alice"}])
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client), tools=tools)

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-1", permission="bash")
    )
    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-2", permission="edit")
    )

    assert await approvals.try_handle_reply("approve", "user-1")
    assert client.permission_replies == []
    assert "`req-1`, `req-2`" in tools.messages_sent[-1]["content"]
    assert approvals.awaiting_human()


async def test_one_resolved_ask_keeps_the_other_parked() -> None:
    """The watcher must stay parked while a second ask still owes a reply,
    otherwise its human-wait time is charged to the compute budget."""
    client = FakeOpencodeClient()
    tools = FakeAgentTools(participants=[{"id": "user-1", "handle": "@alice"}])
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client), tools=tools)

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-1", permission="bash")
    )
    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-2", permission="edit")
    )

    assert await approvals.try_handle_reply("approve req-1", "user-1")
    assert approvals.awaiting_human()


async def test_polite_permission_reply_uses_pending_request() -> None:
    client = FakeOpencodeClient()
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))
    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-1", permission="bash")
    )

    assert await approvals.try_handle_reply("approve please", "user-1")
    assert client.permission_replies[0]["permission_id"] == "req-1"


async def test_reply_to_nonmatching_request_id_is_not_consumed() -> None:
    """A reply naming a different id is not for this pending ask: it is left
    alone (forwarded as an ordinary prompt), not swallowed."""
    client = FakeOpencodeClient()
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-current", permission="bash")
    )

    assert not await approvals.try_handle_reply("approve req-stale", "user-1")
    assert client.permission_replies == []


async def test_notify_room_send_failure_does_not_strand_turn() -> None:
    """The approval-request post is best-effort: if send_message raises, the
    failure is swallowed so the turn still unblocks (``release_turn_wait``
    runs) instead of stranding the paused session or crashing the event loop."""
    released: list[bool] = []
    approvals = make_room_approvals(
        cast(OpencodeClientProtocol, FakeOpencodeClient()),
        tools=RaisingSendTools(),
        release_turn_wait=lambda: released.append(True),
    )

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="req-1", permission="bash")
    )

    assert released == [True]


async def test_question_answer_beginning_with_a_mention_is_preserved() -> None:
    """A free-text answer that legitimately starts with an @handle (naming a
    person) survives: only the leading delivery mention is stripped, not the
    answer's own content."""
    client = FakeOpencodeClient()
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))

    # Questions arrive as wire dicts (the model's before-validator keeps dicts,
    # not OpencodeQuestion instances) -- so a single question actually populates
    # and parse_question_answers takes its one-answer branch.
    await approvals.on_question_asked(
        OpencodeQuestionRequest(
            id="q-1", questions=[{"question": "Who should review?"}]
        )
    )

    assert await approvals.try_handle_reply("@alexander.zaikman/tom @alice", "user-1")
    assert client.question_replies == [{"request_id": "q-1", "answers": [["@alice"]]}]


async def test_mention_only_question_reply_requests_a_real_answer() -> None:
    client = FakeOpencodeClient()
    tools = FakeAgentTools()
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client), tools=tools)
    await approvals.on_question_asked(
        OpencodeQuestionRequest(id="q-1", questions=[{"question": "Who?"}])
    )

    assert await approvals.try_handle_reply("@alexander.zaikman/tom", "user-1")
    assert client.question_replies == []
    assert "waiting for answers" in tools.messages_sent[-1]["content"].lower()


async def test_new_permission_ask_survives_previous_reply() -> None:
    client = BlockingReplyClient("permission")
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))
    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="request-old", permission="bash")
    )

    old_reply = asyncio.create_task(
        approvals.try_handle_reply("approve request-old", "user-1")
    )
    await client.reply_started.wait()
    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="request-new", permission="bash")
    )
    client.allow_reply.set()

    assert await old_reply
    assert await approvals.try_handle_reply("reject request-new", "user-1")
    assert [reply["permission_id"] for reply in client.permission_replies] == [
        "request-old",
        "request-new",
    ]


async def test_new_question_ask_survives_previous_answer() -> None:
    client = BlockingReplyClient("question")
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))
    await approvals.on_question_asked(
        OpencodeQuestionRequest(id="question-old", questions=[{"question": "Old?"}])
    )

    old_reply = asyncio.create_task(approvals.try_handle_reply("old answer", "user-1"))
    await client.reply_started.wait()
    await approvals.on_question_asked(
        OpencodeQuestionRequest(id="question-new", questions=[{"question": "New?"}])
    )
    client.allow_reply.set()

    assert await old_reply
    assert await approvals.try_handle_reply("new answer", "user-1")
    assert [reply["request_id"] for reply in client.question_replies] == [
        "question-old",
        "question-new",
    ]


async def test_new_question_ask_survives_previous_rejection() -> None:
    client = BlockingReplyClient("reject")
    approvals = make_room_approvals(cast(OpencodeClientProtocol, client))
    await approvals.on_question_asked(
        OpencodeQuestionRequest(id="question-old", questions=[{"question": "Old?"}])
    )

    old_reply = asyncio.create_task(approvals.try_handle_reply("reject", "user-1"))
    await client.reply_started.wait()
    await approvals.on_question_asked(
        OpencodeQuestionRequest(id="question-new", questions=[{"question": "New?"}])
    )
    client.allow_reply.set()

    assert await old_reply
    assert await approvals.try_handle_reply("new answer", "user-1")
    assert client.question_rejections == ["question-old"]
    assert [reply["request_id"] for reply in client.question_replies] == [
        "question-new"
    ]


async def test_manual_permission_reply_from_follow_up_message(
    make_adapter, tools
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
        adapter.on_message(
            make_platform_message(content="Please continue"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )

    await wait_for(
        lambda: any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )
    )
    await wait_for(lambda: first_turn.done())
    assert all(msg["content"] != "Approved and done" for msg in tools.messages_sent)
    # Regression: FakeAgentTools records a call made with no mentions instead
    # of rejecting it like the real AgentTools.send_message does, so this must
    # be asserted explicitly -- it silently passed before mentions was wired.
    approval_requested = next(
        m for m in tools.messages_sent if "approval requested" in m["content"].lower()
    )
    assert approval_requested["mentions"]

    await adapter.on_message(
        make_platform_message(content="approve req-1"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )
    await first_turn
    await wait_for(
        lambda: any(
            msg["content"] == "Approved and done" for msg in tools.messages_sent
        )
    )

    assert fake_client.permission_replies == [
        {"session_id": "sess-1", "permission_id": "req-1", "response": "once"}
    ]
    assert any(msg["content"] == "Approved and done" for msg in tools.messages_sent)
    handled_with = next(
        m for m in tools.messages_sent if "handled with" in m["content"].lower()
    )
    assert handled_with["mentions"]


async def test_manual_question_reply_from_follow_up_message(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_question("sess-1", "q-1", "What should I do next?")]
        ],
        reply_question_events={
            "q-1": [
                event_message_updated("sess-1", "msg-4"),
                event_text_part("sess-1", "msg-4", "Question answered"),
                event_session_idle("sess-1"),
            ]
        },
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    first_turn = asyncio.create_task(
        adapter.on_message(
            make_platform_message(content="Need an answer"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )

    await wait_for(
        lambda: any(
            "asked question" in message["content"].lower()
            for message in tools.messages_sent
        )
    )
    await wait_for(lambda: first_turn.done())
    # Regression: FakeAgentTools accepts a call made with no mentions instead
    # of rejecting it like the real AgentTools.send_message does, so this must
    # be asserted explicitly -- it silently passed before mentions was wired.
    asked_question = next(
        m for m in tools.messages_sent if "asked question" in m["content"].lower()
    )
    assert asked_question["mentions"]

    await adapter.on_message(
        make_platform_message(content="Ship the adapter"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    await wait_for(
        lambda: any(
            message["content"] == "Question answered" for message in tools.messages_sent
        )
    )
    assert fake_client.question_replies == [
        {"request_id": "q-1", "answers": [["Ship the adapter"]]}
    ]
    answered = next(
        m
        for m in tools.messages_sent
        if "opencode question" in m["content"].lower()
        and "answered" in m["content"].lower()
    )
    assert answered["mentions"]


async def test_auto_accept_approval_mode() -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-1")],
        ],
        reply_permission_events={
            "perm-1": [
                event_message_updated("sess-1", "msg-auto"),
                event_text_part("sess-1", "msg-auto", "auto accepted"),
                event_session_idle("sess-1"),
            ]
        },
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="auto_accept"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert fake_client.permission_replies == [
        {"session_id": "sess-1", "permission_id": "perm-1", "response": "once"}
    ]
    # No approval prompt sent to user in auto_accept mode
    assert not any(
        "approval requested" in m["content"].lower() for m in tools.messages_sent
    )


async def test_auto_decline_approval_mode() -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-1")],
        ],
        reply_permission_events={"perm-1": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="auto_decline"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert fake_client.permission_replies == [
        {"session_id": "sess-1", "permission_id": "perm-1", "response": "reject"}
    ]


async def test_auto_reject_question_mode() -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_question("sess-1", "q-1", "What to do?")]],
        reject_question_events={"q-1": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(question_mode="auto_reject"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert fake_client.question_rejections == ["q-1"]
    assert not any(
        "asked question" in m["content"].lower() for m in tools.messages_sent
    )


async def test_permission_timeout_expiry() -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_permission("sess-1", "perm-timeout")]],
        reply_permission_events={"perm-timeout": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            approval_mode="manual",
            approval_wait_timeout_s=0.1,
            approval_timeout_reply="reject",
        ),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    await wait_for(lambda: len(fake_client.permission_replies) > 0, timeout_s=3.0)
    assert fake_client.permission_replies[0]["response"] == "reject"
    error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
    assert any("timed out" in e["content"].lower() for e in error_events)

    await adapter.on_cleanup("room-1")


async def test_permission_timeout_does_not_cancel_its_own_reply() -> None:
    client = BlockingReplyClient("permission")
    tools = FakeAgentTools()
    approvals = make_room_approvals(
        cast(OpencodeClientProtocol, client),
        tools=tools,
        config=OpencodeAdapterConfig(approval_wait_timeout_s=0.01),
    )

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="perm-timeout", permission="bash")
    )
    await asyncio.wait_for(client.reply_started.wait(), timeout=1.0)
    client.allow_reply.set()
    await asyncio.wait_for(approvals.wait_until_idle(), timeout=1.0)
    await wait_for(lambda: bool(tools.events_sent))

    assert client.permission_replies[0]["response"] == "reject"
    assert "timed out" in tools.events_sent[0]["content"].lower()


async def test_question_timeout_expiry() -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_question("sess-1", "q-timeout", "Pick a color")]
        ],
        reject_question_events={"q-timeout": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            question_mode="manual",
            question_wait_timeout_s=0.1,
        ),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    await wait_for(lambda: len(fake_client.question_rejections) > 0, timeout_s=3.0)
    assert fake_client.question_rejections == ["q-timeout"]
    error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
    assert any("timed out" in e["content"].lower() for e in error_events)

    await adapter.on_cleanup("room-1")


async def test_question_timeout_does_not_cancel_its_own_rejection() -> None:
    client = BlockingReplyClient("reject")
    tools = FakeAgentTools()
    approvals = make_room_approvals(
        cast(OpencodeClientProtocol, client),
        tools=tools,
        config=OpencodeAdapterConfig(question_wait_timeout_s=0.01),
    )

    await approvals.on_question_asked(
        OpencodeQuestionRequest(
            id="question-timeout",
            questions=[{"question": "Continue?"}],
        )
    )
    await asyncio.wait_for(client.reply_started.wait(), timeout=1.0)
    client.allow_reply.set()
    await asyncio.wait_for(approvals.wait_until_idle(), timeout=1.0)
    await wait_for(lambda: bool(tools.events_sent))

    assert client.question_rejections == ["question-timeout"]
    assert "timed out" in tools.events_sent[0]["content"].lower()


async def test_failed_auto_replies_fail_only_the_affected_turn() -> None:
    failures: list[str] = []
    client = FailingReplyClient()
    approvals = make_room_approvals(
        cast(OpencodeClientProtocol, client),
        fail_turn=failures.append,
        config=OpencodeAdapterConfig(
            approval_mode="auto_accept",
            question_mode="auto_reject",
        ),
    )

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="permission-1", permission="bash")
    )
    await approvals.on_question_asked(
        OpencodeQuestionRequest(
            id="question-1",
            questions=[{"question": "Continue?"}],
        )
    )

    assert failures == [
        "OpenCode failed to reply to permission `permission-1`.",
        "OpenCode failed to reject question `question-1`.",
    ]


async def test_abandoning_a_request_stops_its_expiry_timer() -> None:
    """``_fail_request`` must cancel the timer it pops, or nothing ever can.

    ``cancel()`` reaches only entries still in the registry, so a timer left
    running past its entry's removal keeps the room's state alive — through the
    ``ApprovalPorts`` closures — until the full wait timeout elapses.
    """
    client: dict[str, OpencodeClientProtocol | None] = {
        "current": cast(OpencodeClientProtocol, FakeOpencodeClient())
    }
    approvals = RoomApprovals(
        OpencodeAdapterConfig(approval_mode="manual"),
        ApprovalPorts(
            room_id="room-1",
            session_id=lambda: "sess-1",
            client=lambda: client["current"],
            tools=lambda: cast(AgentToolsProtocol, FakeAgentTools()),
            turn_mentions=lambda: [],
            release_turn_wait=lambda: None,
            fail_turn=lambda _message: None,
            is_own_band_tool=lambda _permission: False,
        ),
    )

    await approvals.on_permission_asked(
        OpencodePermissionRequest(id="perm-1", permission="bash")
    )
    timer = approvals._permissions["perm-1"].timeout_task
    assert timer is not None and not timer.done()

    # The client is gone by the time the human replies — a teardown or a serve
    # restart mid-approval, which sends the reply down the abandonment path.
    client["current"] = None
    await approvals.try_handle_reply("approve perm-1", "user-1")

    approvals.cancel()
    await wait_for(timer.done)


async def test_cleanup_with_pending_permission() -> None:
    """Cleanup mid-permission cancels timeout without crash."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_permission("sess-1", "perm-cleanup")]],
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="manual"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    task = asyncio.create_task(
        adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )

    await wait_for(
        lambda: any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )
    )

    # Cleanup while permission is pending
    await adapter.on_cleanup("room-1")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # No permission reply should have been sent (just cleaned up)
    assert fake_client.permission_replies == []


async def test_cleanup_with_pending_question() -> None:
    """Cleanup mid-question cancels timeout without crash."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_question("sess-1", "q-cleanup", "Something?")]],
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(question_mode="manual"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    task = asyncio.create_task(
        adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )

    await wait_for(
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

    # No question reply should have been sent
    assert fake_client.question_replies == []
    assert fake_client.question_rejections == []


async def test_always_permission_reply_from_follow_up_message(
    make_adapter, tools
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
        adapter.on_message(
            make_platform_message(content="Please continue"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )

    await wait_for(
        lambda: any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )
    )
    await wait_for(lambda: first_turn.done())

    await adapter.on_message(
        make_platform_message(content="always req-always"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )
    await first_turn
    await wait_for(
        lambda: any(msg["content"] == "Always approved" for msg in tools.messages_sent)
    )

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "req-always",
            "response": "always",
        }
    ]


async def test_band_tool_permission_auto_approved_in_manual_mode(
    make_adapter, tools
) -> None:
    """A permission ask naming the adapter's own band tool is granted
    `always` without any room prompt, even in manual mode -- platform
    plumbing must never stall on a human approval."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-band", permission="band_send_message")]
        ],
        reply_permission_events={
            "perm-band": [
                event_message_updated("sess-1", "msg-band"),
                event_text_part("sess-1", "msg-band", "tool ran"),
                event_session_idle("sess-1"),
            ]
        },
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="manual"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await run_single_turn(adapter, tools)

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "perm-band",
            "response": "always",
        }
    ]
    assert not any(
        "approval requested" in m["content"].lower() for m in tools.messages_sent
    )
    assert any(msg["content"] == "tool ran" for msg in tools.messages_sent)


async def test_band_tool_permission_matches_server_prefixed_custom_tool(
    make_adapter, tools
) -> None:
    """OpenCode may report an MCP tool ask under its `{server}_{tool}`
    naming; a server-prefixed custom tool still auto-approves."""

    class EchoInput(BaseModel):
        """Echo text."""

        text: str

    def echo_tool(input_data: EchoInput) -> str:
        return input_data.text

    fake_client = FakeOpencodeClient(
        reply_permission_events={"perm-echo": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="manual"),
        additional_tools=[(EchoInput, echo_tool)],
        client_factory=lambda _config: fake_client,
    )
    await adapter.on_started("OpenCode Agent", "A coding agent")
    # OpenCode prefixes MCP tools with the agent-scoped server name, so the ask
    # names the custom tool as ``{server}_echo``.
    fake_client._prompt_event_sequences = [
        [
            event_permission(
                "sess-1", "perm-echo", permission=f"{adapter._mcp_server_name}_echo"
            )
        ]
    ]
    tools = FakeAgentTools()

    await run_single_turn(adapter, tools)

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "perm-echo",
            "response": "always",
        }
    ]


async def test_band_tool_permission_bypasses_auto_decline() -> None:
    """auto_decline rejects ordinary asks, but the adapter's own band
    tools are still granted -- declining band_store_memory would break
    the platform plumbing the adapter itself registered."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-mem", permission="band_store_memory")]
        ],
        reply_permission_events={"perm-mem": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="auto_decline"),
        capabilities=Capability.MEMORY,
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await run_single_turn(adapter, tools)

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "perm-mem",
            "response": "always",
        }
    ]


async def test_doom_loop_permission_auto_accepted_in_auto_accept_mode(
    make_adapter, tools
) -> None:
    """Pins the E2E-lane behavior: a non-tool ask (doom_loop) under
    auto_accept is granted `once` -- the safety heuristic keeps firing
    server-side, each trip is just answered without a room prompt."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-loop", permission="doom_loop")]
        ],
        reply_permission_events={"perm-loop": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="auto_accept"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await run_single_turn(adapter, tools)

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "perm-loop",
            "response": "once",
        }
    ]
    assert not any(
        "approval requested" in m["content"].lower() for m in tools.messages_sent
    )


async def test_doom_loop_permission_still_relayed_in_manual_mode(
    make_adapter, tools
) -> None:
    """Guards the interactive path: non-band asks keep the manual relay
    (room prompt + reply flow), only the adapter's own tools bypass it."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-loop", permission="doom_loop")]
        ],
        reply_permission_events={
            "perm-loop": [
                event_message_updated("sess-1", "msg-loop"),
                event_text_part("sess-1", "msg-loop", "continued"),
                event_session_idle("sess-1"),
            ]
        },
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="manual"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    first_turn = asyncio.create_task(
        adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )

    await wait_for(
        lambda: any(
            "approval requested for `doom_loop`" in m["content"].lower()
            for m in tools.messages_sent
        )
    )
    await wait_for(lambda: first_turn.done())
    assert fake_client.permission_replies == []

    await adapter.on_message(
        make_platform_message(content="approve perm-loop"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )
    await first_turn

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "perm-loop",
            "response": "once",
        }
    ]
