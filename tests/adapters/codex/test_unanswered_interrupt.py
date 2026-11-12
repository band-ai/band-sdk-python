"""Codex adapter: a timed-out turn whose process ignores the interrupt frees its room."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.turn_lifecycle import BACKEND_CANCEL_TIMEOUT_SECONDS
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import (
    AnswersAStepLaterClient,
    ToolSchemaFakeTools,
    answered_turn,
    send_bootstrap,
)
from tests.adapters.codex.timedturn import (
    INTERRUPTED_FIRST_TURN,
    MARGIN_S,
    failures,
    past_its_total,
    running_turn,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    patch_codex_client,
)

TOTAL_S = 180.0


async def started_adapter(*processes: FakeCodexClient) -> CodexAdapter:
    """A started adapter whose room runs on each of ``processes`` in turn."""
    adapter = CodexAdapter(CodexAdapterConfig(turn_timeout_s=TOTAL_S))
    patch_codex_client(adapter, *processes)
    await adapter.on_started("Codex Agent", "A coding agent")
    return adapter


def deaf_to_interrupts() -> FakeCodexClient:
    """A process that takes turn/interrupt and never answers it."""
    client = FakeCodexClient()
    client.interrupt_released.clear()
    return client


@pytest.mark.looptime
class TestUnansweredInterrupt:
    async def test_the_timeout_is_reported_once_the_interrupt_goes_unanswered(
        self,
    ) -> None:
        """The process gets as long to answer as a stop of the turn would wait."""
        client = deaf_to_interrupts()
        adapter = await started_adapter(client)
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(TOTAL_S + BACKEND_CANCEL_TIMEOUT_SECONDS - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == past_its_total(TOTAL_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

        assert failures(tools) == past_its_total(TOTAL_S)
        assert (
            client.params_of(CodexRequestMethod.TURN_INTERRUPT)
            == INTERRUPTED_FIRST_TURN
        )

    async def test_the_process_that_ignored_the_interrupt_is_replaced(self) -> None:
        """The old process is closed, and the room's thread continues on a new
        one, which answers the next message."""
        client = deaf_to_interrupts()
        client.script(*answered_turn())
        replacement = FakeCodexClient(events=answered_turn())
        adapter = await started_adapter(client, replacement)
        timed_out, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        await send_bootstrap(adapter, tools=ToolSchemaFakeTools())
        client.turn_started.clear()

        async with running_turn(adapter, timed_out) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(TOTAL_S + BACKEND_CANCEL_TIMEOUT_SECONDS + MARGIN_S)
            assert failures(timed_out) == past_its_total(TOTAL_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        await send_bootstrap(adapter, tools=tools)

        assert client.closed
        assert [
            resumed["threadId"]
            for resumed in replacement.params_of(CodexRequestMethod.THREAD_RESUME)
        ] == [FIRST_THREAD_ID]
        assert len(replacement.params_of(CodexRequestMethod.TURN_START)) == 1
        assert failures(tools) == []

    async def test_a_process_that_answers_the_interrupt_keeps_its_room(self) -> None:
        client, spare = FakeCodexClient(), FakeCodexClient()
        adapter = await started_adapter(client, spare)
        timed_out, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, timed_out) as turn:
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        client.script(*answered_turn("turn-2"))
        await send_bootstrap(adapter, tools=tools)

        assert failures(timed_out) == past_its_total(TOTAL_S)
        assert not client.closed
        assert spare.requests == []
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        assert failures(tools) == []

    @pytest.mark.parametrize(
        "settle_s", [0.0, 600.0], ids=["no-settle-grace", "long-settle-grace"]
    )
    async def test_the_wait_for_the_interrupt_ignores_the_settle_grace(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        settle_s: float,
    ) -> None:
        """The settle grace is how long a leave or stop lets a turn wind down;
        however short or long, an unanswered interrupt waits the same."""
        client = deaf_to_interrupts()
        adapter = await started_codex(
            client, turn_timeout_s=TOTAL_S, turn_settle_timeout_s=settle_s
        )
        tools = ToolSchemaFakeTools()

        async with running_turn(adapter, tools) as turn:
            await client.turn_started.wait()
            await asyncio.sleep(TOTAL_S + BACKEND_CANCEL_TIMEOUT_SECONDS - MARGIN_S)
            assert not turn.done()
            assert failures(tools) == []

            await asyncio.sleep(2 * MARGIN_S)
            assert failures(tools) == past_its_total(TOTAL_S)
            with pytest.raises(TurnResultAlreadyReported):
                await turn

    async def test_an_answer_one_step_later_keeps_the_room_without_a_settle_grace(
        self, started_codex: Callable[..., Awaitable[CodexAdapter]]
    ) -> None:
        client, spare = AnswersAStepLaterClient(), FakeCodexClient()
        adapter = await started_codex(
            client, spare, turn_timeout_s=TOTAL_S, turn_settle_timeout_s=0
        )
        timed_out, tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()

        async with running_turn(adapter, timed_out) as turn:
            with pytest.raises(TurnResultAlreadyReported):
                await turn
        client.script(*answered_turn("turn-2"))
        await send_bootstrap(adapter, tools=tools)

        assert failures(timed_out) == past_its_total(TOTAL_S)
        assert not client.closed
        assert spare.requests == []
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        assert failures(tools) == []
