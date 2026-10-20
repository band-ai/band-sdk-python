"""Backend cancellation joins the sole owner of the installed SDK client."""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import ClaudeSDKClient

from band.client.streaming import ControlMode
from band.core.retention import Residency
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import OpenRoom


class TestBackendRelease:
    async def test_stop_joins_one_disconnect_without_swallowing_cleanup_errors(
        self,
        claude_room: OpenRoom,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        disconnect = ClaudeSDKClient.disconnect
        disconnected: list[ClaudeSDKClient] = []

        async def counted_disconnect(client: ClaudeSDKClient) -> None:
            disconnected.append(client)
            await disconnect(client)

        monkeypatch.setattr(ClaudeSDKClient, "disconnect", counted_disconnect)
        room = await claude_room()
        work, closing = Hold(), Hold()
        room.claude.script([work])
        room.claude.closing = closing
        sending = room.send_in_background("work")
        settled: asyncio.Task[None] | None = None
        try:
            await work.arrived()
            session = room.session_manager.session(room.room_id)
            assert session is not None
            sending.cancel()
            await room.adapter.on_interrupt(room.room_id, ControlMode.STOP)
            await closing.arrived()
            joining = asyncio.Event()

            async def join_backend_stop() -> None:
                joining.set()
                await room.adapter.backend_turns.drain()

            settled = asyncio.create_task(join_backend_stop())
            await joining.wait()
            assert not settled.done()
            assert len(disconnected) == 1
            assert room.claude.sessions[0].alive

            closing.released.set()
            await settled
            with pytest.raises(asyncio.CancelledError):
                await sending
            assert len(disconnected) == 1
            assert not room.claude.sessions[0].alive
            assert session.retained.snapshot.state is Residency.SUSPENDED
            assert session.retained.snapshot.error is None
            assert not [record for record in caplog.records if record.exc_info]
        finally:
            work.released.set()
            closing.released.set()
            await asyncio.gather(sending, return_exceptions=True)
            if settled is not None:
                await settled
