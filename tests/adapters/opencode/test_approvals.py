"""Tests for OpencodeAdapter."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import (
    AdapterFeatures,
    Capability,
)
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools


from .helpers import (
    FakeOpencodeClient,
    _run_single_turn,
    event_message_updated,
    event_permission,
    event_question,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
    wait_for,
)


async def test_manual_permission_reply_from_follow_up_message() -> None:
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
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
    tools = FakeAgentTools()

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


async def test_manual_question_reply_from_follow_up_message() -> None:
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
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
    tools = FakeAgentTools()

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


async def test_always_permission_reply_from_follow_up_message() -> None:
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
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
    tools = FakeAgentTools()

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


async def test_band_tool_permission_auto_approved_in_manual_mode() -> None:
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

    await _run_single_turn(adapter, tools)

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


async def test_band_tool_permission_matches_server_prefixed_custom_tool() -> None:
    """OpenCode may report an MCP tool ask under its `{server}_{tool}`
    naming; a server-prefixed custom tool still auto-approves."""

    class EchoInput(BaseModel):
        """Echo text."""

        text: str

    def echo_tool(input_data: EchoInput) -> str:
        return input_data.text

    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-echo", permission="band_echo")]
        ],
        reply_permission_events={"perm-echo": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="manual"),
        additional_tools=[(EchoInput, echo_tool)],
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await _run_single_turn(adapter, tools)

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
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await _run_single_turn(adapter, tools)

    assert fake_client.permission_replies == [
        {
            "session_id": "sess-1",
            "permission_id": "perm-mem",
            "response": "always",
        }
    ]


async def test_doom_loop_permission_auto_accepted_in_auto_accept_mode() -> None:
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

    await _run_single_turn(adapter, tools)

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


async def test_doom_loop_permission_still_relayed_in_manual_mode() -> None:
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
