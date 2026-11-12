"""Codex adapter: a process that does not answer a request its turn waits on frees its room."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import (
    CODEX_TRANSITION_TIMEOUT_S,
    CodexAdapter,
    CodexAdapterConfig,
)
from band.client.streaming import ControlMode
from band.core.protocols import FAILURE_CODE_TIMEOUT, TurnResultAlreadyReported
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    UnansweringClient,
    agent_input,
    answered_turn,
    send_bootstrap,
)
from tests.adapters.codex.timedturn import MARGIN_S, failures, running_turn
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    patch_codex_client,
)
from tests.adapters.dispatch import deliver_event
from tests.adapters.waiting import until

# A thread Band history records for the room, which its first turn resumes.
RECORDED_THREAD = "thr-recorded"
RECORDED = CodexSessionState(room_to_thread={ROOM_ID: RECORDED_THREAD})
# Each request a room's first turn waits on before it runs, the history that
# makes the turn send it, and the name its failure gives it.
BEFORE_A_TURN_RUNS = pytest.mark.parametrize(
    ("request_method", "history", "named"),
    [
        pytest.param(CodexRequestMethod.THREAD_START, None, "thread_start"),
        pytest.param(CodexRequestMethod.THREAD_RESUME, RECORDED, "thread_resume"),
        pytest.param(CodexRequestMethod.TURN_START, None, "turn_start"),
    ],
    ids=["thread-start", "thread-resume", "turn-start"],
)


def unanswered(named: str) -> list[tuple[str, str]]:
    """The one failure of a turn whose ``named`` request Codex never answered."""
    bound = CODEX_TRANSITION_TIMEOUT_S
    text = f"Codex turn timed out after {bound}s of silence ({named})"
    return [(FAILURE_CODE_TIMEOUT, text)]


async def started_adapter(*processes: FakeCodexClient) -> CodexAdapter:
    """A started adapter whose room runs on each of ``processes`` in turn."""
    adapter = CodexAdapter(CodexAdapterConfig())
    patch_codex_client(adapter, *processes)
    await adapter.on_started("Codex Agent", "A coding agent")
    return adapter


@pytest.mark.looptime
class TestUnansweredStart:
    @BEFORE_A_TURN_RUNS
    async def test_an_unanswered_request_is_reported_once_as_a_timeout(
        self,
        request_method: CodexRequestMethod,
        history: CodexSessionState | None,
        named: str,
    ) -> None:
        """Codex gets as long as a process gets to start; no turn limit runs yet."""
        client = UnansweringClient(request_method)
        adapter = await started_adapter(client)
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools, history=history) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == unanswered(named)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert failures(tools) == unanswered(named)

    @BEFORE_A_TURN_RUNS
    async def test_the_process_that_did_not_answer_is_replaced(
        self,
        request_method: CodexRequestMethod,
        history: CodexSessionState | None,
        named: str,
    ) -> None:
        """The old process is closed and a new one answers the next message."""
        client = UnansweringClient(request_method)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_adapter(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, unheard, history=history) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S + MARGIN_S)
            assert failures(unheard) == unanswered(named)
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await send_bootstrap(adapter, tools=tools, history=history)

        assert client.closed
        assert len(replacement.params_of(CodexRequestMethod.TURN_START)) == 1
        assert failures(tools) == []

    async def test_a_recorded_thread_survives_an_unanswered_history_resume(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """The failed turn was the room's first, so its next message is an
        ordinary one. It still continues the thread Band history records."""
        client = UnansweringClient(CodexRequestMethod.THREAD_RESUME)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, unheard, history=RECORDED) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S + MARGIN_S)
            assert failures(unheard) == unanswered("thread_resume")
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await deliver_event(
            adapter, agent_input("and now?", tools, is_session_bootstrap=False)
        )

        assert [
            resumed["threadId"]
            for resumed in replacement.params_of(CodexRequestMethod.THREAD_RESUME)
        ] == [RECORDED_THREAD]
        assert replacement.params_of(CodexRequestMethod.THREAD_START) == []
        assert [
            started["threadId"]
            for started in replacement.params_of(CodexRequestMethod.TURN_START)
        ] == [RECORDED_THREAD]
        assert failures(tools) == []

    async def test_a_stop_during_an_unanswered_thread_start_replaces_the_process(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        """A stop before the bound must not leave the silent process for the
        room's next message to wait on again."""
        client = UnansweringClient(CodexRequestMethod.THREAD_START)
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_codex(client, replacement)
        stopped, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, stopped):
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S / 2)
            await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await send_bootstrap(adapter, tools=tools)

        assert client.closed
        assert len(client.params_of(CodexRequestMethod.THREAD_START)) == 1
        assert len(replacement.params_of(CodexRequestMethod.TURN_START)) == 1
        assert failures(stopped) == []
        assert failures(tools) == []

    async def test_a_seeded_thread_continues_after_an_unanswered_turn_start(
        self,
    ) -> None:
        client = UnansweringClient(
            CodexRequestMethod.TURN_START, answered=1, events=answered_turn()
        )
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_adapter(client, replacement)
        unheard, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        await send_bootstrap(adapter, tools=ToolSchemaFakeTools())

        async with running_turn(adapter, unheard) as turn:
            await client.asked.wait()
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S + MARGIN_S)
            assert failures(unheard) == unanswered("turn_start")
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await send_bootstrap(adapter, tools=tools)

        assert client.closed
        assert [
            resumed["threadId"]
            for resumed in replacement.params_of(CodexRequestMethod.THREAD_RESUME)
        ] == [FIRST_THREAD_ID]
        assert failures(tools) == []

    async def test_an_answer_that_comes_within_the_bound_keeps_the_process(
        self,
    ) -> None:
        client, spare = FakeCodexClient(events=answered_turn()), FakeCodexClient()
        client.turn_start_released.clear()
        adapter = await started_adapter(client, spare)
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await until(
                lambda: bool(client.params_of(CodexRequestMethod.TURN_START)),
                failure="The adapter never sent turn/start",
            )
            await asyncio.sleep(CODEX_TRANSITION_TIMEOUT_S - MARGIN_S)
            assert failures(tools) == []
            client.turn_start_released.set()
            await turn

        assert failures(tools) == []
        assert not client.closed
        assert spare.requests == []
