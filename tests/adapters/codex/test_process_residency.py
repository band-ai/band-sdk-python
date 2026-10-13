"""Codex adapter: a room's process starts within its budget and stays up until it is released for a reason Codex survives."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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
)
from tests.adapters.codexturns import (
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
        # The abandoned start was cancelled before its handshake finished and
        # closed; only the second message's start completed.
        assert (client.starts, client.closes) == (1, 1)
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 1

    async def test_an_idle_release_is_refused_and_keeps_the_process(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            tool_call_request(1, BandTool.NO_REPLY), turn_completed()
        )
        await room.send("work")
        resident = room.adapter._room_clients[ROOM_ID]

        outcome = await room.adapter._release(resident, ReleaseCause.IDLE)

        assert outcome == ReleaseOutcome.BUSY
        assert not room.client.closed
        assert resident.reader is not None and not resident.reader.done()
