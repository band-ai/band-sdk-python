"""Independent terminal evidence remains reachable after interrupted room teardown."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.opencode.helpers import ObservedOpencodeAdapter, event_session_idle
from tests.adapters.opencode.stopconfirmation.peer import OpencodePeer, turn
from tests.toolkit.tasks import background


class TestTerminalRouting:
    async def test_first_submission_failure_keeps_the_independent_idle_route(
        self, adapter: ObservedOpencodeAdapter, peer: OpencodePeer
    ) -> None:
        peer.fail_first = True
        async with background(adapter.on_event(turn())) as first:
            await peer.first_prompt.arrived()
            await adapter.watcher_started.wait()
            peer.first_prompt.released.set()
            with pytest.raises(httpx.HTTPStatusError):
                await first
        await peer.aborted.wait()
        await adapter.backend_turns.drain()
        with pytest.raises(InputNotAccepted) as refused:
            await adapter.on_event(turn())
        await peer.events.enqueue(event_session_idle(peer.sessions[0])).wait()
        assert refused.value.ready is not None and refused.value.ready.done()
        await adapter.on_event(turn())
        assert peer.prompts == peer.sessions == ["session-1", "session-2"]

    async def test_departed_room_still_observes_idle_on_the_shared_stream(
        self, adapter: ObservedOpencodeAdapter, peer: OpencodePeer
    ) -> None:
        async with background(adapter.on_event(turn())) as first:
            await peer.first_prompt.arrived()
            await adapter.watcher_started.wait()
            await adapter.on_event(turn("room-2"))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await peer.aborted.wait()
        await adapter.backend_turns.drain()
        await adapter.on_cleanup("room-1")
        with pytest.raises(InputNotAccepted) as refused:
            await adapter.on_event(turn())
        await peer.events.enqueue(event_session_idle(peer.sessions[0])).wait()
        assert refused.value.ready is not None and refused.value.ready.done()
        await adapter.on_event(turn())
        assert peer.prompts == peer.sessions == ["session-1", "session-2", "session-3"]

    async def test_last_departed_room_keeps_only_its_terminal_observer_until_idle(
        self, adapter: ObservedOpencodeAdapter, peer: OpencodePeer
    ) -> None:
        async with background(adapter.on_event(turn())) as first:
            await peer.first_prompt.arrived()
            await adapter.watcher_started.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await peer.aborted.wait()
        await adapter.backend_turns.drain()
        await adapter.on_cleanup("room-1")
        ready = adapter.backend_turns.blocker("room-1")
        assert ready is not None and not ready.done()
        stream = peer.events
        stream.enqueue(event_session_idle(peer.sessions[0]))
        await stream.closed.wait()
        assert ready.done()
        await adapter.on_event(turn())
        assert peer.prompts == peer.sessions == ["session-1", "session-2"]

    async def test_idle_before_cancellation_cannot_create_an_unconfirmed_stop(
        self, adapter: ObservedOpencodeAdapter, peer: OpencodePeer
    ) -> None:
        async with background(adapter.on_event(turn())) as first:
            await peer.first_prompt.arrived()
            await adapter.watcher_started.wait()
            await peer.events.enqueue(event_session_idle(peer.sessions[0])).wait()
            assert adapter.backend_turns.active_handle("room-1") is None
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await adapter.backend_turns.drain()
        assert adapter.backend_turns.blocker("room-1") is None
        await adapter.on_event(turn())
        assert len(peer.prompts) == 2

    async def test_adapter_shutdown_releases_transport_without_claiming_remote_stop(
        self, adapter: ObservedOpencodeAdapter, peer: OpencodePeer
    ) -> None:
        async with background(adapter.on_event(turn())) as first:
            await peer.first_prompt.arrived()
            await adapter.watcher_started.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await peer.aborted.wait()
        await adapter.backend_turns.drain()
        await adapter.cleanup_all()
        await peer.events.closed.wait()
        ready = adapter.backend_turns.blocker("room-1")
        assert ready is not None and not ready.done()
