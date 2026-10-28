"""Reject native permission callbacks while the room's turn is stopping."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.client.streaming import ControlMode
from band.integrations.acp.client_adapter import ACPTurnTimeoutError
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.adapters.antigravity_acp.control import PermissionControl
from tests.adapters.antigravity_acp.kit import AntigravityRoom, mcp_permission
from tests.adapters.antigravity_acp.peerconfig import PeerSettings
from tests.framework_conformance.turnprobes import ROOM_ID
from tests.integrations.acp.acp_toolkit.agent import Gate, allow_or_reject
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock

REJECT_ID = allow_or_reject()[1].option_id


class TestAntigravityPermissionStopping:
    async def test_a_callback_is_declined_while_cancellation_is_pending(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        permission_control: PermissionControl,
        hold_native_cancel: Callable[[AntigravityRoom], Awaitable[Gate]],
    ) -> None:
        peer = await open_peer(
            PeerSettings(
                permission=mcp_permission(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE),
                permission_on_prompt=False,
                hold_prompt=True,
                reply=None,
                control_port=permission_control.port,
            )
        )
        async with background(peer.send()) as turn:
            await permission_control.prompted()
            cancellation = await hold_native_cancel(peer)
            try:
                await peer.adapter.on_interrupt(ROOM_ID, ControlMode.INTERRUPT)
                await cancellation.received.wait()

                await permission_control.request()

                assert peer.adapter.backend_turns.stopping(ROOM_ID)
                assert peer.permission_choices() == [REJECT_ID]
            finally:
                turn.cancel()
                try:
                    await permission_control.release_prompt()
                finally:
                    cancellation.release.set()
                    await peer.adapter.backend_turns.drain()

            with pytest.raises(asyncio.CancelledError):
                await turn

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_callback_is_declined_while_deadline_cleanup_is_pending(
        self,
        open_peer: Callable[..., Awaitable[AntigravityRoom]],
        permission_control: PermissionControl,
        hold_native_cancel: Callable[[AntigravityRoom], Awaitable[Gate]],
    ) -> None:
        async with controlled_clock() as clock:
            peer = await clock.run_io(
                open_peer(
                    PeerSettings(
                        permission=mcp_permission(
                            BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE
                        ),
                        permission_on_prompt=False,
                        hold_prompt=True,
                        reply=None,
                        control_port=permission_control.port,
                    ),
                    config={"turn_timeout_s": 10},
                )
            )
            try:
                async with clock.background(peer.send()) as turn:
                    await clock.run_io(permission_control.prompted())
                    cancellation = await hold_native_cancel(peer)
                    try:
                        await clock.elapse(peer.adapter.config.turn_timeout_s)
                        await cancellation.received.wait()

                        await clock.run_io(permission_control.request())

                        assert peer.adapter.backend_turns.stopping(ROOM_ID)
                        assert peer.permission_choices() == [REJECT_ID]
                    finally:
                        try:
                            await clock.run_io(permission_control.release_prompt())
                        finally:
                            cancellation.release.set()
                            await clock.run_io(peer.adapter.backend_turns.drain())

                    with pytest.raises(ACPTurnTimeoutError):
                        await clock.run_io(turn)
            finally:
                await clock.run_io(peer.adapter.cleanup_all())
