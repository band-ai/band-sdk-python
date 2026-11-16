"""Codex adapter: a stop during an unanswered turn start retires the process before the room serves again."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import ControlMode
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import ROOM_ID, CodexRoom, UnansweredRequestClient
from tests.adapters.codexturns import (
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.waiting import spin, until
from tests.toolkit.tasks import background


class TestStopDuringUnansweredStart:
    @pytest.mark.looptime
    async def test_the_process_is_retired_and_a_late_answer_never_interrupts(
        self,
    ) -> None:
        client = UnansweredRequestClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()],
            held=CodexRequestMethod.TURN_START,
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        room = CodexRoom(adapter, client)
        try:
            async with background(room.send("first")) as sending:
                await client.unanswered.arrived()
                await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
                with pytest.raises(asyncio.CancelledError):
                    await sending
            await client.close_finished.wait()
            # No turn id yet: stop must retire the process, not invent an interrupt.
            assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []

            # The stop abandons its wait for the unanswered start's identity.
            await adapter.backend_turns.settled(ROOM_ID)
            await until(
                lambda: adapter.backend_turns.blocker(ROOM_ID) is None,
                failure="the room never became ready after its process closed",
            )
            await room.send("second")
            client.unanswered.released.set()
            await spin()

            # Replacement already served (turn-1). A late start that lands on
            # the new process may be cancelled as an orphan; it must not
            # interrupt the successor or leave the room blocked.
            assert (client.starts, client.closes) == (2, 1)
            assert all(
                interrupt.get("turnId") != "turn-1"
                for interrupt in client.params_of(CodexRequestMethod.TURN_INTERRUPT)
            )
            assert adapter.backend_turns.blocker(ROOM_ID) is None
        finally:
            client.unanswered.released.set()
            await adapter.on_cleanup(ROOM_ID)
