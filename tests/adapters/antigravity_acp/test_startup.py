"""Allow healthy native initialization beyond the generic acquisition deadline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.adapters.antigravity_acp.control import PeerControl
from tests.adapters.antigravity_acp.kit import AntigravityRoom
from tests.adapters.antigravity_acp.peerconfig import (
    DEFAULT_REPLY,
    PeerSettings,
)
from tests.integrations.acp.acp_toolkit.harness import Reply
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


class TestAntigravityStartup:
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_default_acquisition_allows_slow_initialization_and_usable_session(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        peer_control: PeerControl,
    ) -> None:
        async with controlled_clock() as clock:
            peer = await clock.run_io(
                open_peer(
                    PeerSettings(hold_initialize=True, control_port=peer_control.port)
                )
            )
            try:
                async with clock.background(peer.send()) as turn:
                    await clock.run_io(peer_control.initializing())

                    await clock.elapse(35)

                    await clock.run_io(peer_control.release_initialize())
                    tools = await clock.run_io(turn)
                followup = await clock.run_io(peer.send())

                assert Reply(messages=tools.messages_sent).texts == [DEFAULT_REPLY]
                assert Reply(messages=followup.messages_sent).texts == [DEFAULT_REPLY]
            finally:
                await clock.run_io(peer.adapter.cleanup_all())

            assert await clock.run_io(peer_control.disconnected()) == b""
