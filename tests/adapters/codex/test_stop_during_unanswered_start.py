"""Codex adapter: a stop during an unanswered start request retires the process before the room serves again."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import ControlMode
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from band.runtime.tools import BandTool
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    ToolSchemaFakeTools,
    UnansweredRequestClient,
    answered_turn,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.waiting import spin, until
from tests.toolkit.tasks import background

# Band history naming a thread for the room, which its first turn resumes.
RECORDED = CodexSessionState(room_to_thread={ROOM_ID: "thr-recorded"})


class TestStopDuringUnansweredStart:
    @pytest.mark.looptime
    @pytest.mark.parametrize(
        ("held", "history"),
        [
            pytest.param(CodexRequestMethod.THREAD_START, None, id="thread-start"),
            pytest.param(
                CodexRequestMethod.THREAD_RESUME, RECORDED, id="thread-resume"
            ),
        ],
    )
    async def test_a_stop_during_an_unanswered_thread_request_retires_the_process(
        self, held: CodexRequestMethod, history: CodexSessionState | None
    ) -> None:
        """The room's next message must not wait on the silent process again."""
        client = UnansweredRequestClient(events=answered_turn(), held=held)
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        stopped, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        try:
            async with background(
                send_bootstrap(adapter, tools=stopped, history=history)
            ) as sending:
                await client.unanswered.arrived()
                await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
                with pytest.raises(asyncio.CancelledError):
                    await sending
            await send_bootstrap(adapter, tools=tools, history=history)

            assert (client.starts, client.closes) == (2, 1)
            assert len(client.params_of(CodexRequestMethod.TURN_START)) == 1
            assert reported_failures(stopped) == reported_failures(tools) == []
        finally:
            client.unanswered.released.set()
            await adapter.on_cleanup(ROOM_ID)

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
