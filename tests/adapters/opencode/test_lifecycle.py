"""Tests for OpencodeAdapter."""

from __future__ import annotations

import asyncio
from contextlib import suppress
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


from tests.adapters.opencode.helpers import (
    FakeMCPBackend,
    FakeOpencodeClient,
    make_fake_mcp_backend_factory,
    event_message_updated,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
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
        make_fake_mcp_backend_factory(fake_backend),
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
    """Interleaved turns keep events and platform tools scoped by room."""

    class InterleavedClient(FakeOpencodeClient):
        def __init__(self) -> None:
            super().__init__()
            self.started_sessions: set[str] = set()
            self.both_started = asyncio.Event()

        async def prompt_async(self, session_id: str, **kwargs: Any) -> None:  # pyrefly: ignore[bad-override]
            await super().prompt_async(session_id, **kwargs)
            self.started_sessions.add(session_id)
            if len(self.started_sessions) == 2:
                self.both_started.set()
            await self.both_started.wait()
            suffix = session_id.removeprefix("sess-")
            await self._queue.put(event_message_updated(session_id, f"msg-r{suffix}"))
            await self._queue.put(
                event_text_part(session_id, f"msg-r{suffix}", f"reply to room {suffix}")
            )
            await self._queue.put(event_session_idle(session_id))

    fake_client = InterleavedClient()
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
    tools_r1 = FakeAgentTools()
    tools_r2 = FakeAgentTools()

    await adapter.on_started("OpenCode Agent", "A coding agent")

    async def run_room(room_number: int, room_tools: FakeAgentTools) -> None:
        room_id = f"room-{room_number}"
        await adapter.on_message(
            make_platform_message(room_id=room_id, content=f"hello room {room_number}"),
            tools_protocol(room_tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=room_id,
        )

    await asyncio.gather(
        run_room(1, tools_r1),
        run_room(2, tools_r2),
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


async def test_concurrent_room_start_waits_for_mcp_registration() -> None:
    """Every room start waits for the shared client's MCP startup barrier."""

    class SlowRegistrationClient(FakeOpencodeClient):
        def __init__(self) -> None:
            super().__init__()
            self.registration_started = asyncio.Event()
            self.release_registration = asyncio.Event()

        async def register_mcp_server(self, *, name: str, url: str) -> dict[str, Any]:
            self.registration_started.set()
            await self.release_registration.wait()
            return await super().register_mcp_server(name=name, url=url)

    fake_client = SlowRegistrationClient()
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)

    first = asyncio.create_task(adapter._ensure_client_started())
    await fake_client.registration_started.wait()
    second = asyncio.create_task(adapter._ensure_client_started())
    await asyncio.sleep(0)

    assert not second.done()

    fake_client.release_registration.set()
    await asyncio.gather(first, second)

    assert len(fake_client.registered_mcp_servers) == 1
    await adapter._shutdown_client()


async def test_teardown_does_not_disconnect_a_successor_registration(tools) -> None:
    """A superseded teardown must not strip the next client's MCP registration.

    ``opencode serve`` keys MCP registrations globally by name, so a late
    name-only disconnect from the outgoing client would unregister the
    incoming one — which has no path back, since registration only runs on
    the turn that creates the client.
    """
    serve: dict[str, str] = {}

    class BlockingDisconnectClient(FakeOpencodeClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(serve_registrations=serve, **kwargs)
            self.disconnect_started = asyncio.Event()
            self.release_disconnect = asyncio.Event()

        async def disconnect_mcp_server(self, name: str) -> None:
            self.disconnect_started.set()
            await self.release_disconnect.wait()
            await super().disconnect_mcp_server(name)

    outgoing = BlockingDisconnectClient(
        prompt_event_sequences=[[event_session_idle("sess-1")]]
    )
    incoming = FakeOpencodeClient(
        serve_registrations=serve,
        prompt_event_sequences=[[event_session_idle("sess-1")]],
    )
    clients: list[FakeOpencodeClient] = [outgoing, incoming]
    adapter = OpencodeAdapter(client_factory=lambda _config: clients.pop(0))

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await run_single_turn(adapter, tools)

    cleanup = asyncio.create_task(adapter.on_cleanup("room-1"))
    await outgoing.disconnect_started.wait()

    successor = asyncio.create_task(
        adapter.on_message(
            make_platform_message(room_id="room-2", content="next room"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-2",
        )
    )
    # Give the successor every chance to register ahead of the pending
    # disconnect; correct teardown holds it off instead.
    with suppress(TimeoutError):
        await asyncio.wait_for(asyncio.shield(successor), 0.2)

    outgoing.release_disconnect.set()
    await asyncio.gather(cleanup, successor)

    assert incoming.registered_mcp_servers, "successor never registered"
    assert adapter._mcp_server_name in serve

    await adapter.on_cleanup("room-2")


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
