"""Tests for OpencodeAdapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch


from band.adapters.opencode import OpencodeAdapter
from band.core.types import (
    AdapterFeatures,
    Emit,
    TurnUsage,
)
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools
from tests.adapters.usage_events import recorded_usage_payloads


from .helpers import (
    FakeMCPBackend,
    FakeOpencodeClient,
    _make_fake_mcp_backend_factory,
    event_message_updated,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
    wait_for,
)


async def test_watch_task_drains_the_turn_that_started_it() -> None:
    """Regression: the turn's future and usage dict are snapshotted before
    the prompt await. When the turn completes while prompt_async's POST is
    still open and a racing message begins the next turn, the resumed
    on_message must still drain ITS turn's usage, not the new turn's
    (empty) dict."""
    fake_client = FakeOpencodeClient(prompt_event_sequences=[[]])
    adapter = OpencodeAdapter(
        client_factory=lambda _config: fake_client,
        features=AdapterFeatures(emit={Emit.USAGE}),
    )
    tools = FakeAgentTools()
    await adapter.on_started("OpenCode Agent", "A coding agent")

    room_state = await adapter._get_or_create_room_state("room-1")
    orig_prompt = fake_client.prompt_async

    async def racing_prompt(*args: Any, **kwargs: Any) -> None:
        # This turn's usage arrives and the turn completes while the
        # prompt POST is still open...
        room_state.usage_by_message["msg-1"] = TurnUsage(
            input_tokens=100, output_tokens=20
        )
        adapter._finish_turn(room_state)
        # ...and a racing message begins (and finishes) the next turn
        # before the first on_message resumes.
        adapter._begin_turn(room_state, sender_id="user-2")
        adapter._finish_turn(room_state)
        await orig_prompt(*args, **kwargs)

    with patch.object(fake_client, "prompt_async", racing_prompt):
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    usage_payloads = recorded_usage_payloads(tools)
    assert usage_payloads == [
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ], f"expected the first turn's snapshot to be drained, got {usage_payloads}"


async def test_new_turn_does_not_wipe_prior_turns_pending_usage(
    make_adapter, tools
) -> None:
    """Regression: a message racing in between turn completion and the usage
    drain must not empty the prior turn's usage. The dict is turn-owned (a
    fresh instance per _begin_turn), so the watch task sums the instance it
    captured, not whatever the room currently points at."""
    adapter = OpencodeAdapter(
        client_factory=lambda _config: FakeOpencodeClient(),
        features=AdapterFeatures(emit={Emit.USAGE}),
    )
    tools = FakeAgentTools()
    room_state = await adapter._get_or_create_room_state("room-1")
    room_state.tools = tools_protocol(tools)

    adapter._begin_turn(room_state, sender_id="user-1")
    room_state.usage_by_message["msg-1"] = TurnUsage(input_tokens=100, output_tokens=20)
    # What on_message hands this turn's watch task.
    first_turn_usage = room_state.usage_by_message

    # The next turn begins before the first turn's usage is drained.
    adapter._begin_turn(room_state, sender_id="user-2")
    assert room_state.usage_by_message == {}

    await adapter._emit_turn_usage(room_state, first_turn_usage)

    usage_payloads = recorded_usage_payloads(tools)
    assert usage_payloads == [
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ], f"expected the first turn's usage to survive, got {usage_payloads}"


async def test_cleanup_is_idempotent(make_adapter, tools) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-5"),
                event_text_part("sess-1", "msg-5", "done"),
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

    await adapter.on_cleanup("room-1")
    await adapter.on_cleanup("room-1")
    assert fake_client.closed is True


async def test_cleanup_race_creates_a_fresh_client_for_the_next_room(
    make_adapter, tools
) -> None:
    stop_started = asyncio.Event()
    stop_release = asyncio.Event()
    fake_backend = FakeMCPBackend(
        stop_started=stop_started,
        stop_release=stop_release,
    )
    first_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "first"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    second_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-2"),
                event_text_part("sess-1", "msg-2", "second"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    clients = [first_client, second_client]
    adapter = OpencodeAdapter(
        client_factory=lambda _config: clients.pop(0),
    )
    tools = FakeAgentTools()

    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        _make_fake_mcp_backend_factory(fake_backend),
    ):
        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        cleanup_task = asyncio.create_task(adapter.on_cleanup("room-1"))
        await wait_for(stop_started.is_set)

        await adapter.on_message(
            make_platform_message(room_id="room-2", content="next room"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-2",
        )

        stop_release.set()
        await cleanup_task

    assert len(first_client.prompt_calls) == 1
    assert len(second_client.prompt_calls) == 1
    assert second_client.closed is False

    await adapter.on_cleanup("room-2")
    assert second_client.closed is True


async def test_concurrent_message_rejected(make_adapter, tools) -> None:
    """Sending a second message while a turn is active returns an error."""
    # First prompt never completes (no session.idle event)
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_message_updated("sess-1", "msg-long")],
            [],  # second prompt gets empty events
        ]
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    first_task = asyncio.create_task(
        adapter.on_message(
            make_platform_message(content="first"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
    )
    # Wait for first turn to start
    await wait_for(lambda: len(fake_client.prompt_calls) > 0)

    # Send second message while first is active
    await adapter.on_message(
        make_platform_message(content="second"),
        tools_protocol(tools),
        OpencodeSessionState(session_id="sess-1", room_id="room-1"),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    # Second message should get rejected with "still processing" error
    error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
    assert any("still processing" in e["content"].lower() for e in error_events)
    assert len(fake_client.prompt_calls) == 1

    # Clean up: cancel the first task
    first_task.cancel()
    try:
        await first_task
    except asyncio.CancelledError:
        pass
    await adapter.on_cleanup("room-1")


async def test_two_rooms_active_concurrently(tools) -> None:
    """Two rooms with separate sessions route events correctly."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            # Room 1 prompt events
            [
                event_message_updated("sess-1", "msg-r1"),
                event_text_part("sess-1", "msg-r1", "reply to room 1"),
                event_session_idle("sess-1"),
            ],
            # Room 2 prompt events
            [
                event_message_updated("sess-2", "msg-r2"),
                event_text_part("sess-2", "msg-r2", "reply to room 2"),
                event_session_idle("sess-2"),
            ],
        ]
    )
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
    tools_r1 = FakeAgentTools()
    tools_r2 = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")

    # Start room 1
    await adapter.on_message(
        make_platform_message(room_id="room-1", content="hello room 1"),
        tools_protocol(tools_r1),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    # Start room 2 (shared client, different session)
    await adapter.on_message(
        make_platform_message(room_id="room-2", content="hello room 2"),
        tools_protocol(tools_r2),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-2",
    )

    # Each room got its own session
    assert len(fake_client.created_sessions) == 2
    assert fake_client.created_sessions[0]["id"] == "sess-1"
    assert fake_client.created_sessions[1]["id"] == "sess-2"

    # Each room received the correct reply
    assert any("reply to room 1" in m["content"] for m in tools_r1.messages_sent)
    assert any("reply to room 2" in m["content"] for m in tools_r2.messages_sent)

    # Cleanup room 1 while room 2 state is still tracked
    await adapter.on_cleanup("room-1")
    # Client should still be alive (room 2 exists)
    assert not fake_client.closed
    assert fake_client.disconnected_mcp_servers == []

    # Cleanup room 2 shuts down the client
    await adapter.on_cleanup("room-2")
    assert fake_client.closed
    assert fake_client.disconnected_mcp_servers == [adapter._mcp_server_name]


async def test_shutdown_rechecks_for_room_arriving_after_cleanup_decision(
    tools,
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_idle("sess-1")]]
    )
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(room_id="room-1"),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    await adapter._get_or_create_room_state("room-2")
    await adapter._shutdown_client()

    assert not fake_client.closed
    assert fake_client.disconnected_mcp_servers == []

    await adapter.on_cleanup("room-1")
    await adapter.on_cleanup("room-2")
