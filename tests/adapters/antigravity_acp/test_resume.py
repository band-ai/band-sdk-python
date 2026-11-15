"""Restore the room's persisted provider session from its own home."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerEvent


class TestAntigravityResume:
    async def test_a_replacement_adapter_loads_the_recorded_session(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        first = await open_peer()
        tools = await first.send()
        [created] = first.records(PeerEvent.CREATED)
        await first.adapter.cleanup_all()
        second = await open_peer(
            config={"gemini_home": first.adapter.config.gemini_home}
        )

        await second.send(history=tools.events_sent)

        assert second.records(PeerEvent.LOADED) == [
            {"event": PeerEvent.LOADED, "session": created["session"]}
        ]
        assert second.records(PeerEvent.CREATED) == []
