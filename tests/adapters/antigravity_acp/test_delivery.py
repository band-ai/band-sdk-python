"""Keep native text separate from replies delivered through Band tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.antigravity_acp import ROOM_DELIVERY_GUIDANCE
from band.core.protocols import TurnResultAlreadyReported
from band.testing.fake_tools import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import PeerEvent, PeerSettings
from tests.framework_conformance.turnprobes import turn_tools


class TestAntigravityDelivery:
    async def test_native_text_alone_is_reported_as_a_missing_reply(
        self, open_peer: Callable[..., Awaitable[AntigravityRoom]]
    ) -> None:
        peer = await open_peer(
            PeerSettings(reply=None, text="A native-only response."),
            config={"custom_section": "Keep the user's own guidance."},
        )

        tools = turn_tools()
        with pytest.raises(TurnResultAlreadyReported):
            await peer.send(tools=tools)

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert tools.chat == []
        [prompt] = peer.records(PeerEvent.PROMPTED)
        assert ROOM_DELIVERY_GUIDANCE in prompt["text"]
        assert "Keep the user's own guidance." in prompt["text"]
