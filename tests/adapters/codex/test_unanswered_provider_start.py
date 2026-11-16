"""Codex adapter: a thread or turn start left unanswered is abandoned and its process replaced."""

from __future__ import annotations

import pytest

from band.adapters.codex import PROCESS_NOT_READY_ERROR, CodexAdapterConfig
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.core.retention import RoomRetentionConfig
from band.integrations.codex.types import CodexRequestMethod
from band.runtime.tools import BandTool
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    CountingCloseClient,
    UnansweredRequestClient,
    thread_of,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.waiting import spin


async def room_serving(
    held: CodexRequestMethod,
) -> tuple[CodexRoom, UnansweredRequestClient]:
    """A started room whose first ``held`` request the app-server never answers."""
    client = UnansweredRequestClient(
        events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()],
        held=held,
    )
    adapter = make_codex_adapter(
        client,
        config=CodexAdapterConfig(
            retention=RoomRetentionConfig(transition_timeout_s=1)
        ),
    )
    await adapter.on_started("Codex Agent", "A coding agent")
    return CodexRoom(adapter, client), client


class TestUnansweredProviderStart:
    @pytest.mark.looptime
    async def test_an_unanswered_thread_start_never_publishes_its_late_thread(
        self,
    ) -> None:
        room, client = await room_serving(CodexRequestMethod.THREAD_START)
        try:
            with pytest.raises(TimeoutError):
                await room.send("first")
            await client.close_finished.wait()
            assert thread_of(room.adapter, ROOM_ID) is None

            client.unanswered.released.set()
            await room.send("second")

            assert [f["message"] for f in reported_failures(room.deliveries[0])] == [
                PROCESS_NOT_READY_ERROR
            ]
            assert (client.starts, client.closes) == (2, 1)
            assert thread_of(room.adapter, ROOM_ID) != FIRST_THREAD_ID
            assert [
                params["threadId"]
                for params in client.params_of(CodexRequestMethod.TURN_START)
            ] == [thread_of(room.adapter, ROOM_ID)]
        finally:
            client.unanswered.released.set()
            await room.adapter.on_cleanup(ROOM_ID)

    @pytest.mark.looptime
    async def test_an_unanswered_turn_start_is_retired_with_its_process_not_interrupted(
        self,
    ) -> None:
        room, client = await room_serving(CodexRequestMethod.TURN_START)
        try:
            with pytest.raises(TimeoutError):
                await room.send("first")
            await client.close_finished.wait()

            await room.send("second")
            client.unanswered.released.set()
            await spin()

            assert [f["message"] for f in reported_failures(room.deliveries[0])] == [
                PROCESS_NOT_READY_ERROR
            ]
            assert (client.starts, client.closes) == (2, 1)
            assert client.params_of(CodexRequestMethod.TURN_INTERRUPT) == []
            assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        finally:
            client.unanswered.released.set()
            await room.adapter.on_cleanup(ROOM_ID)

    @pytest.mark.looptime
    async def test_a_provider_timeout_on_turn_start_keeps_the_process(
        self,
    ) -> None:
        """A finished turn/start TimeoutError is not a start-budget abandon."""
        client = CountingCloseClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()],
            turn_start_error=TimeoutError("rpc timed out"),
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        room = CodexRoom(adapter, client)
        try:
            with pytest.raises(TimeoutError, match="rpc timed out"):
                await room.send("first")
            assert (client.starts, client.closes) == (1, 0)
            assert adapter.backend_turns.unconfirmed_handle(ROOM_ID) is None
            assert [f["message"] for f in reported_failures(room.deliveries[0])] == [
                GENERIC_PROVIDER_FAILURE_MESSAGE
            ]

            await room.send("second")
            assert (client.starts, client.closes) == (1, 0)
            assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        finally:
            await adapter.on_cleanup(ROOM_ID)
