"""Codex adapter: a turn past its deadline never reuses the process it interrupted."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import FAILURE_CODE_TIMEOUT, TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from band.testing import reported_failures
from tests.adapters.codex.kit import ROOM_ID, CodexRoom, CountingCloseClient
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestTurnTimeoutRecovery:
    @pytest.mark.looptime
    async def test_an_unanswered_interrupt_replaces_the_process_before_the_next_turn(
        self,
    ) -> None:
        client = CountingCloseClient(events=[])
        client.interrupt_released.clear()
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(turn_timeout_s=0.01)
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        room = CodexRoom(adapter, client)
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await room.send("first")
            client.script(
                tool_call_request(1, BandTool.NO_REPLY), turn_completed("turn-2")
            )
            await room.send("second")

            assert [
                failure["code"] for failure in reported_failures(room.deliveries[0])
            ] == [FAILURE_CODE_TIMEOUT]
            assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == [
                {"threadId": FIRST_THREAD_ID, "turnId": "turn-1"}
            ]
            assert (client.starts, client.closes) == (2, 1)
            assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        finally:
            client.interrupt_released.set()
            await adapter.on_cleanup(ROOM_ID)

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        "interrupt_error",
        [None, ConnectionResetError("Codex process is gone")],
        ids=["interrupt-answered", "interrupt-failed"],
    )
    async def test_an_answered_or_failed_interrupt_still_replaces_the_process(
        self, interrupt_error: Exception | None
    ) -> None:
        client = CountingCloseClient(events=[], interrupt_error=interrupt_error)
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(turn_timeout_s=0.01)
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        room = CodexRoom(adapter, client)
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await room.send("first")
            client.script(
                tool_call_request(1, BandTool.NO_REPLY), turn_completed("turn-2")
            )
            await room.send("second")

            assert (client.starts, client.closes) == (2, 1)
            assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        finally:
            await adapter.on_cleanup(ROOM_ID)
