"""Tests for OpencodeAdapter."""

from __future__ import annotations

import asyncio
import json


from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import (
    Capability,
    Emit,
)
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools
from tests.adapters.usage_events import recorded_usage_payloads


from tests.adapters.opencode.helpers import (
    AnyHTTPStatusError,
    FakeOpencodeClient,
    TaskEventFailingTools,
    run_single_turn,
    event_message_updated,
    event_message_updated_with_tokens,
    event_part_delta,
    event_permission,
    event_reasoning_part,
    event_session_error,
    event_session_idle,
    event_text_part,
    event_tool_part,
    event_user_message_updated,
    make_platform_message,
    tools_protocol,
    wait_for,
)


async def test_prompt_submission_failure_does_not_leave_room_stuck(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-5"),
                event_text_part("sess-1", "msg-5", "Recovered after failure"),
                event_session_idle("sess-1"),
            ]
        ],
        prompt_exceptions=[AnyHTTPStatusError(500, "sess-1")],
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(content="first try"),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    await adapter.on_message(
        make_platform_message(content="second try"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    assert len(fake_client.prompt_calls) == 2
    assert not any(
        "still processing the previous request" in event["content"].lower()
        for event in tools.events_sent
    )
    assert any(
        message["content"] == "Recovered after failure"
        for message in tools.messages_sent
    )


async def test_reports_tool_events_when_enabled() -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_tool_part(
                    "sess-1",
                    "msg-4",
                    tool="bash",
                    call_id="call-1",
                    status="running",
                    input_data={"command": "pytest"},
                ),
                event_tool_part(
                    "sess-1",
                    "msg-4",
                    tool="bash",
                    call_id="call-1",
                    status="completed",
                    input_data={"command": "pytest"},
                    output="ok",
                ),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = OpencodeAdapter(
        client_factory=lambda _config: fake_client,
        emit=Emit.TOOL_CALLS,
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

    tool_calls = [e for e in tools.events_sent if e["message_type"] == "tool_call"]
    tool_results = [e for e in tools.events_sent if e["message_type"] == "tool_result"]
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    assert json.loads(tool_calls[0]["content"])["name"] == "bash"
    assert json.loads(tool_results[0]["content"])["output"] == "ok"


async def test_preserves_falsy_tool_result_outputs_when_reporting(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_tool_part(
                    "sess-1",
                    "msg-7",
                    tool="bash",
                    call_id="call-2",
                    status="completed",
                    input_data={"command": "printf 0"},
                    output=0,
                ),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = OpencodeAdapter(
        client_factory=lambda _config: fake_client,
        emit=Emit.TOOL_CALLS,
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

    tool_results = [e for e in tools.events_sent if e["message_type"] == "tool_result"]
    assert len(tool_results) == 1
    assert json.loads(tool_results[0]["content"])["output"] == 0


async def test_does_not_echo_user_text_parts_as_assistant_output(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_user_message_updated("sess-1", "msg-user"),
                event_text_part("sess-1", "msg-user", "user prompt text"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(provider_id="openai", model_id="gpt-5.5"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(content="user prompt text"),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert tools.messages_sent[0]["content"] == (
        "OpenCode completed the turn without a text reply."
    )


async def test_ignores_reasoning_deltas_and_relays_final_text_only(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-assistant"),
                event_reasoning_part(
                    "sess-1",
                    "msg-assistant",
                    part_id="part-reasoning",
                ),
                event_part_delta(
                    "sess-1",
                    "msg-assistant",
                    "part-reasoning",
                    'The user wants "pong".',
                ),
                event_text_part("sess-1", "msg-assistant", ""),
                event_part_delta(
                    "sess-1",
                    "msg-assistant",
                    "part-msg-assistant",
                    "pong",
                ),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(content="Reply with exactly: pong"),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert tools.messages_sent[0]["content"] == "pong"


async def test_session_error_emits_error_event(make_adapter, tools) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_error("sess-1", "boom")]]
    )
    adapter = make_adapter(fake_client)

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

    error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
    assert error_events
    assert "boom" in error_events[0]["content"].lower()


async def test_turn_timeout_aborts_session_and_emits_error() -> None:
    """A turn that never reaches session.idle times out, aborts the
    OpenCode session, and reports an error instead of hanging the room."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[]],  # no events at all; the turn never finishes
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(turn_timeout_s=0.05),
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

    assert fake_client.aborted_sessions == ["sess-1"]
    error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
    assert any("timed out" in e["content"].lower() for e in error_events)

    await adapter.on_cleanup("room-1")


async def test_emits_turn_usage_folding_reasoning_into_output(
    make_adapter, tools
) -> None:
    """Emit.USAGE aggregates the assistant message's ``tokens``, folding
    OpenCode's disjoint ``reasoning`` count into ``output_tokens``."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated_with_tokens(
                    "sess-1",
                    "msg-1",
                    {
                        "input": 10,
                        "output": 5,
                        "reasoning": 3,
                        "cache": {"read": 1, "write": 2},
                    },
                ),
                event_text_part("sess-1", "msg-1", "done"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = OpencodeAdapter(
        client_factory=lambda _config: fake_client,
        emit=Emit.USAGE,
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

    assert recorded_usage_payloads(tools) == [
        {
            "input_tokens": 10,
            "output_tokens": 8,
            "cache_read_tokens": 1,
            "cache_write_tokens": 2,
        }
    ]


async def test_malformed_events_do_not_kill_event_loop(make_adapter, tools) -> None:
    """Junk SSE payloads degrade to ignored events; the turn that follows
    them completes normally instead of the event loop dying mid-stream."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                {"type": "bizarre.event", "properties": {"sessionID": "sess-1"}},
                {"type": "permission.asked", "properties": "garbage"},
                {},
                {"type": "message.updated", "properties": {"info": "not-a-dict"}},
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "survived the junk"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)

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

    assert any(msg["content"] == "survived the junk" for msg in tools.messages_sent)

    await adapter.on_cleanup("room-1")


async def test_tool_reports_canonicalize_server_prefixed_names(
    make_adapter, tools
) -> None:
    """OpenCode surfaces a remote MCP server's tools as `{server}_{tool}`
    (band_store_memory arrives as band_band_store_memory); reported
    tool_call events must carry the canonical band name so consumers
    match one vocabulary across all adapters."""
    fake_client = FakeOpencodeClient()
    adapter = OpencodeAdapter(
        client_factory=lambda _config: fake_client,
        capabilities=Capability.MEMORY,
        emit=Emit.TOOL_CALLS,
    )
    await adapter.on_started("OpenCode Agent", "A coding agent")
    # OpenCode prefixes the band MCP tool with the agent-scoped server name
    # (band_store_memory -> {server}_band_store_memory).
    prefixed = f"{adapter._mcp_server_name}_band_store_memory"
    fake_client._prompt_event_sequences = [
        [
            event_message_updated("sess-1", "msg-1"),
            event_tool_part(
                "sess-1",
                "msg-1",
                tool=prefixed,
                call_id="call-1",
                status="running",
                input_data={"content": "note"},
            ),
            event_tool_part(
                "sess-1",
                "msg-1",
                tool=prefixed,
                call_id="call-1",
                status="completed",
                input_data={"content": "note"},
                output="stored",
            ),
            event_session_idle("sess-1"),
        ]
    ]
    tools = FakeAgentTools()

    await run_single_turn(adapter, tools)

    tool_calls = [
        json.loads(e["content"])
        for e in tools.events_sent
        if e["message_type"] == "tool_call"
    ]
    assert [c["name"] for c in tool_calls] == ["band_store_memory"]


async def test_manual_relay_releases_turn_when_mentionless_send_rejected(
    make_adapter, tools
) -> None:
    """A sender-less turn yields no mentions, which the platform rejects.
    The manual approval relay must still release the turn (best-effort
    post) rather than stranding on_message until the turn timeout."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_permission("sess-1", "perm-loop", permission="doom_loop")]
        ],
        reply_permission_events={"perm-loop": [event_session_idle("sess-1")]},
    )
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(approval_mode="manual"),
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    # No hang: on_message returns once the relay releases the turn wait,
    # even though the mention-less room post was rejected.
    await asyncio.wait_for(
        adapter.on_message(
            make_platform_message(sender_id="", sender_name=""),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        ),
        timeout=5,
    )

    # Relay was attempted and dropped (nothing recorded), and manual mode
    # did not auto-reply the pending permission.
    assert tools.messages_sent == []
    assert fake_client.permission_replies == []
    await adapter.on_cleanup("room-1")


async def test_turn_completes_when_fallback_reply_send_rejected(
    make_adapter, tools
) -> None:
    """A sender-less turn's reply has no one to @mention, so the platform
    rejects it. The watch task must still release on_message (release is
    in finally) instead of stranding it on the captured release_future."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "here is the reply"),
                event_session_idle("sess-1"),
            ]
        ],
    )
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
    tools = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await asyncio.wait_for(
        adapter.on_message(
            make_platform_message(sender_id="", sender_name=""),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        ),
        timeout=5,
    )

    # The reply could not be delivered (no mention), so it surfaced as an
    # error event rather than hanging or vanishing silently.
    assert tools.messages_sent == []
    assert any(e["message_type"] == "error" for e in tools.events_sent)
    await adapter.on_cleanup("room-1")


async def test_room_posting_tool_reply_suppresses_text_fallback(
    make_adapter, tools
) -> None:
    """When the model replies via band_send_message, the adapter must not also
    post the assistant's plain text (double-post). Detection holds without
    execution reporting: Emit.TOOL_CALLS governs only the tool_call/tool_result
    narration, not the text-fallback suppression."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "I sent it via the tool."),
                event_tool_part(
                    "sess-1",
                    "msg-1",
                    tool="band_send_message",
                    call_id="c1",
                    status="completed",
                    input_data={"content": "hi"},
                ),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)

    await run_single_turn(adapter, tools)

    # The tool (not executed by the fake) was the reply; the fallback stays
    # silent, so the adapter posts no message of its own.
    assert tools.messages_sent == []


async def test_non_room_posting_tool_does_not_suppress_text(
    make_adapter, tools
) -> None:
    """A non-posting tool (bash) is not a reply, so the assistant text is still
    delivered -- suppression must not over-reach."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "Ran the command."),
                event_tool_part(
                    "sess-1",
                    "msg-1",
                    tool="bash",
                    call_id="c1",
                    status="completed",
                    input_data={"command": "ls"},
                ),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)

    await run_single_turn(adapter, tools)

    assert [m["content"] for m in tools.messages_sent] == ["Ran the command."]


async def test_task_event_post_failure_does_not_drop_the_turn(make_adapter) -> None:
    """A transient failure posting the session task event must not abort the
    turn before the model runs -- otherwise the user's message is silently
    dropped. The event is best-effort; the prompt still goes out and the reply
    lands."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part(
                    "sess-1", "msg-1", "Handled despite the event failure."
                ),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)
    tools = TaskEventFailingTools()

    await run_single_turn(adapter, tools)

    assert len(fake_client.prompt_calls) == 1
    assert [m["content"] for m in tools.messages_sent] == [
        "Handled despite the event failure."
    ]
    assert not any(
        "failed while processing" in e["content"].lower() for e in tools.events_sent
    )


async def test_manual_approval_pauses_the_turn_timeout(make_adapter, tools) -> None:
    """A human deliberating over a manual permission must not be charged to the
    compute budget: with a tiny turn_timeout_s but a generous approval window,
    the watcher must NOT abort the parked turn, and the reply must land once the
    human approves."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_permission("sess-1", "req-1"),
            ]
        ],
        reply_permission_events={
            "req-1": [
                event_text_part("sess-1", "msg-1", "Approved and done."),
                event_session_idle("sess-1"),
            ]
        },
    )
    config = OpencodeAdapterConfig(
        approval_mode="manual",
        turn_timeout_s=0.3,
        approval_wait_timeout_s=10.0,
    )
    adapter = make_adapter(fake_client, config=config)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    # Returns once the permission ask releases the turn wait.
    await adapter.on_message(
        make_platform_message(content="run it"),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    # Deliberate well past turn_timeout_s while parked on the human.
    await asyncio.sleep(0.6)
    assert fake_client.aborted_sessions == []

    # The human approves via a room reply; the held turn completes and delivers.
    await adapter.on_message(
        make_platform_message(content="approve req-1"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    await wait_for(
        lambda: any("Approved and done." in m["content"] for m in tools.messages_sent)
    )
    assert fake_client.aborted_sessions == []


async def test_approval_wait_does_not_shorten_the_resumed_turn(
    make_adapter, tools
) -> None:
    """``turn_timeout_s`` bounds compute, not deliberation — including when the
    human replies *before* the deadline. The work resumed after the approval
    must get its own full budget instead of whatever the wait left over."""

    class LateFinishClient(FakeOpencodeClient):
        """Finishes the turn 0.3s after the approval lands."""

        async def reply_permission(
            self, session_id: str, permission_id: str, *, response: str
        ) -> None:
            await super().reply_permission(session_id, permission_id, response=response)

            async def finish() -> None:
                await asyncio.sleep(0.3)
                await self.push_event(event_text_part("sess-1", "msg-1", "Done late."))
                await self.push_event(event_session_idle("sess-1"))

            asyncio.create_task(finish())

    fake_client = LateFinishClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_permission("sess-1", "req-1"),
            ]
        ],
    )
    # 0.25s of deliberation + 0.3s of work exceeds the 0.4s budget, but only
    # 0.3s of it is compute.
    config = OpencodeAdapterConfig(
        approval_mode="manual",
        turn_timeout_s=0.4,
        approval_wait_timeout_s=10.0,
    )
    adapter = make_adapter(fake_client, config=config)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(content="run it"),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    await asyncio.sleep(0.25)
    await adapter.on_message(
        make_platform_message(content="approve req-1"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    await wait_for(
        lambda: any("Done late." in m["content"] for m in tools.messages_sent),
        timeout_s=2.0,
    )
    assert fake_client.aborted_sessions == []
    assert not any(
        "timed out" in event["content"].lower() for event in tools.events_sent
    )


async def test_session_saved_before_registration_tracking_is_reused(tools) -> None:
    """Upgrading must not discard sessions persisted without a registration name.

    ``mcp_server_name`` is only written to the session task event by adapters
    that record it, so every session saved before that carries None. Treating
    the unknown name as ours keeps an existing room's server-side conversation
    instead of silently starting over on the first turn after deploy.
    """
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_idle("sess-legacy")]]
    )
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-legacy", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    assert fake_client.created_sessions == [], "abandoned a recoverable session"
    assert [call["session_id"] for call in fake_client.prompt_calls] == ["sess-legacy"]
