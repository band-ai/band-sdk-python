"""Codex adapter: a room's process starts within its budget and stays up until it is released for a reason Codex survives."""

from __future__ import annotations

import pytest

from band.adapters.codex import PROCESS_NOT_READY_ERROR, CodexAdapterConfig
from band.core.retention import ReleaseCause, ReleaseOutcome, RoomRetentionConfig
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    HeldStartClient,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestProcessResidency:
    """A room's process starts within its budget and stays up until it is
    released for a reason Codex survives."""

    @pytest.mark.looptime
    async def test_a_start_over_its_budget_fails_as_not_ready_and_the_next_starts_fresh(
        self,
    ) -> None:
        client = HeldStartClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()]
        )
        adapter = make_codex_adapter(
            client,
            config=CodexAdapterConfig(
                retention=RoomRetentionConfig(transition_timeout_s=1)
            ),
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        room = CodexRoom(adapter, client)

        with pytest.raises(TimeoutError):
            await room.send("first")
        client.finish_start.set()
        await room.send("second")

        assert [f["message"] for f in reported_failures(room.deliveries[0])] == [
            PROCESS_NOT_READY_ERROR
        ]
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 1

    async def test_an_idle_release_is_refused_and_keeps_the_process(self) -> None:
        client = FakeCodexClient(events=[turn_completed()])
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter)
        room = adapter._room_clients[ROOM_ID]

        outcome = await adapter._release(room, ReleaseCause.IDLE)

        assert outcome == ReleaseOutcome.BUSY
        assert not client.closed
        assert room.reader is not None and not room.reader.done()
        await adapter.on_cleanup(ROOM_ID)
