"""Launch the real stdio boundary and deliver a Band tool reply."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerEvent, PeerSettings


class TestAntigravityStdio:
    async def test_the_native_peer_authenticates_and_replies_through_band(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        room = await open_peer(PeerSettings(reply="Delivered through Band."))

        tools = await room.send()

        assert tools.chat == ["Delivered through Band."]
        assert room.records(PeerEvent.AUTHENTICATED) == [
            {"event": PeerEvent.AUTHENTICATED, "method": "gemini-api-key"}
        ]
