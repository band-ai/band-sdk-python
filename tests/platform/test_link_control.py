"""Tests for BandLink agent.control signal routing.

Control signals must be delivered preemptively — NOT enqueued on the
serialized _event_queue (which would make a control signal wait behind the
very message cycle it is meant to interrupt).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import band.platform.link as link_mod
from band.client.streaming import AgentControlPayload
from band.platform.link import BandLink


class TestOnControlRouting:
    async def test_on_control_invokes_registered_hook(self, link: BandLink):
        """_on_control should call the registered on_control hook with the payload."""
        received: asyncio.Future[AgentControlPayload] = (
            asyncio.get_running_loop().create_future()
        )

        async def hook(payload: AgentControlPayload) -> None:
            received.set_result(payload)

        link.on_control = hook
        payload = AgentControlPayload(
            mode="interrupt", scope="room", agent_id="agent-123", room_id="room-1"
        )

        await link._on_control(payload)

        assert await asyncio.wait_for(received, timeout=5.0) == payload

    async def test_on_control_does_not_enqueue(self, link: BandLink):
        """Control must bypass the serialized event queue entirely."""
        link.on_control = AsyncMock()
        payload = AgentControlPayload(mode="stop", scope="agent", agent_id="agent-123")

        await link._on_control(payload)

        assert link._event_queue.empty()

    async def test_on_control_without_hook_is_safe_noop(self, link: BandLink):
        """A control push with no hook registered must not raise or enqueue."""
        payload = AgentControlPayload(mode="play", scope="agent", agent_id="agent-123")

        await link._on_control(payload)  # should not raise

        assert link._event_queue.empty()

    async def test_connect_wires_on_control_to_channel(self):
        """connect() must pass _on_control into join_agent_control_channel."""
        link = BandLink(
            agent_id="agent-123", api_key="k", ws_url="wss://x/ws", rest_url="https://x"
        )

        fake_ws = AsyncMock()
        fake_ws.__aenter__ = AsyncMock(return_value=fake_ws)
        fake_ws.join_agent_control_channel = AsyncMock()

        async def fake_factory(*args, **kwargs):
            return fake_ws

        # Patch WebSocketClient construction to return our fake.
        orig = link_mod.WebSocketClient
        link_mod.WebSocketClient = lambda *a, **k: fake_ws  # type: ignore[assignment]
        try:
            await link.connect()
        finally:
            link_mod.WebSocketClient = orig

        fake_ws.join_agent_control_channel.assert_awaited_once()
        kwargs = fake_ws.join_agent_control_channel.await_args.kwargs
        assert kwargs.get("on_control") == link._on_control
