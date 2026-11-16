"""A turn abandoned locally revokes its origin and leaves its room unavailable."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import ControlMode
from band.core.exceptions import ProviderUnavailableError
from band.core.protocols import FAILURE_CODE_TIMEOUT, TurnResultAlreadyReported
from band.runtime.tools import CHAT_ID_FIELD_NAME, BandTool
from band.testing import reported_failures
from tests.adapters.letta.unconfirmed.kit import (
    MODES,
    StreamingMode,
    adapter_over_recording_server,
    new_tools,
    refused_as_unavailable,
)
from tests.adapters.lettakit import band_mcp_registrations
from tests.framework_conformance.stopprobes import (
    TURN_TIMEOUT_SECONDS,
    cancel_turn,
    wait_until,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


class TestLocalAbandonment:
    @pytest.mark.parametrize("mode", MODES)
    async def test_a_stop_blocks_the_room_before_and_after_its_cancel_answers(
        self, mode: StreamingMode
    ) -> None:
        adapter, client = adapter_over_recording_server(mode)
        client.hold()
        release_cancel = client.hold_cancel()
        stopped = new_tools()
        turn = asyncio.create_task(adapter.on_event(turn_input(stopped)))
        await wait_until(lambda: bool(client.sent))
        await cancel_turn(turn)
        await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await wait_until(client.cancel_started.is_set)

        # The cancel is still unanswered; the origin is already revoked.
        await refused_as_unavailable(adapter)
        send_message = band_mcp_registrations(adapter)[BandTool.SEND_MESSAGE]
        with pytest.raises(ValueError, match="No tools available"):
            await send_message.execute(
                {"content": "late", "mentions": ["@alice"], CHAT_ID_FIELD_NAME: ROOM_ID}
            )

        release_cancel.set()
        client.release.set()
        await adapter.backend_turns.drain()
        await refused_as_unavailable(adapter)
        await adapter.on_cleanup(ROOM_ID)
        await refused_as_unavailable(adapter)

        assert client.journal == [mode.sent, mode.cancel]
        assert stopped.messages_sent == []
        assert adapter.backend_turns.unavailable(ROOM_ID)

    @pytest.mark.looptime
    @pytest.mark.parametrize("mode", MODES)
    async def test_a_timeout_reports_then_leaves_the_room_unavailable(
        self, mode: StreamingMode
    ) -> None:
        adapter, client = adapter_over_recording_server(
            mode, turn_timeout_s=TURN_TIMEOUT_SECONDS
        )
        client.hold()
        timed_out = new_tools()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(timed_out))
        await adapter.backend_turns.drain()
        client.release.set()
        with pytest.raises(ProviderUnavailableError):
            await adapter.on_event(turn_input(new_tools()))

        assert [f["code"] for f in reported_failures(timed_out)] == [
            FAILURE_CODE_TIMEOUT
        ]
        assert client.journal == [mode.sent, mode.cancel]
