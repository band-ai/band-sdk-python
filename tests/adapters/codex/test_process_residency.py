"""Codex adapter: a room's process starts within its budget."""

from __future__ import annotations

import pytest

from band.adapters.codex import PROCESS_NOT_READY_ERROR, CodexAdapterConfig
from band.core.retention import RoomRetentionConfig
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    CodexRoom,
    HeldStartClient,
)
from tests.adapters.codexturns import (
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestProcessResidency:
    """A room's process starts within its budget."""

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
