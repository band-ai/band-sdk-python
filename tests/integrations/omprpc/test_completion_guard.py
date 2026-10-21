"""Completion protection: stuck results, settlement, drains, and lost replies.

Every shape reproduces a demonstrated stall (failing before the guard) and
asserts the bounded, typed disposition with honest ownership and retirement
semantics. Budgets are shrunk via ``COMPLETION_TIMEOUT_S`` and synchronized on
peer frames, never on sleeps that the assertion depends on.
"""

from __future__ import annotations

import asyncio

import pytest

from band.integrations import omprpc
from band.integrations.omprpc import (
    OmpCompletionTimeout,
    OmpProcessError,
    OmpRpcClient,
)
from band.integrations.ompwire import OmpHostToolCall, OmpToolResult
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    prompt_task,
    second_prompt,
)

GUARD_S = 0.25


@pytest.fixture
def short_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(omprpc, "COMPLETION_TIMEOUT_S", GUARD_S)


class TestCompletionGuard:
    @pytest.mark.parametrize(
        "scenario", ["no-result", "stale-idle-result", "lost-settlement"]
    )
    async def test_stuck_completion_fails_bounded_with_uncertainty(
        self,
        client_factory: ClientFactory,
        short_guard: None,
        scenario: str,
    ) -> None:
        client = await client_factory(scenario)
        observer = Observer()
        running = prompt_task(client, observer)
        await observer.wait_for("agent_end")
        async with asyncio.timeout(10):
            with pytest.raises(OmpCompletionTimeout, match="uncertain"):
                await running
        # The transport retires with the failure: it is not left reusable.
        with pytest.raises(OmpProcessError):
            await client.request("get_state")

    async def test_silent_background_completion_recovers_by_resnapshot(
        self, client_factory: ClientFactory, short_guard: None
    ) -> None:
        # Settlement, newer busy continuation, then the completion's settle
        # frame is lost: waiting on frames alone would stall forever (red
        # before), and a naive timer would false-fire on the busy window
        # (REQ2). The settlement wait must re-observe native state on wake
        # and finish from positive proof, without any guard failure.
        client = await client_factory("silent-settled-continuation")
        observer = Observer()
        running = prompt_task(client, observer)
        claimed = None
        while claimed is None:
            event = await observer.wait_for("agent_end")
            if event.awaitingAsyncWork is True:
                claimed = event
        await client.request("checkpoint")  # the claim was observed in full
        assert not running.done()  # no settle frame exists for the completion
        await client.request("release")  # completes; emits no settle frame
        async with asyncio.timeout(10):
            await running

    async def test_blocked_event_drain_fails_bounded_after_settlement(
        self, client_factory: ClientFactory, short_guard: None
    ) -> None:
        client = await client_factory("drain-blocked-event")
        drain = asyncio.Event()
        observer = Observer()

        async def on_event(event) -> None:
            if event.messageId == "blocked":
                await drain.wait()
            await observer.on_event(event)

        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=on_event,
                on_tool=observer.on_tool,
                on_ui=observer.on_ui,
            )
        )
        try:
            async with asyncio.timeout(10):
                with pytest.raises(OmpCompletionTimeout, match="uncertain"):
                    await running
        finally:
            drain.set()
            await asyncio.gather(running, return_exceptions=True)

    async def test_cancellation_resistant_callback_fails_and_cannot_retire(
        self, client_factory: ClientFactory, short_guard: None
    ) -> None:
        client = await client_factory("drain-stubborn")
        resumed = asyncio.Event()
        cancellations = 0

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            nonlocal cancellations
            while not resumed.is_set():
                try:
                    await resumed.wait()
                except asyncio.CancelledError:
                    cancellations += 1
            return OmpToolResult(content=[])

        observer = Observer()
        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=on_tool,
                on_ui=observer.on_ui,
            )
        )
        async with asyncio.timeout(10):
            with pytest.raises(OmpCompletionTimeout, match="uncertain"):
                await running
        assert cancellations >= 1  # the loop absorbed cancellation
        # Retirement is unproven while a callback task survives: restarts fail
        # closed instead of overlapping ownership (REQ3).
        with pytest.raises(OmpProcessError, match="retirement is unproven"):
            await client.start()
        resumed.set()

    @pytest.mark.parametrize(
        "scenario", ["no-result", "lost-settlement", "get-state-silent"]
    )
    async def test_cancellation_during_completion_keeps_stop_semantics(
        self, client_factory: ClientFactory, short_guard: None, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        observer = Observer()
        running = prompt_task(client, observer)
        await observer.wait_for("agent_end")
        await asyncio.sleep(GUARD_S / 5)  # inside the armed window, before firing
        running.cancel()
        async with asyncio.timeout(10):
            with pytest.raises(asyncio.CancelledError):
                await running
        assert process_is_gone(client)

    async def test_lost_snapshot_response_fails_bounded_at_the_request_layer(
        self, client_factory: ClientFactory, short_guard: None
    ) -> None:
        client = await client_factory("get-state-silent")
        observer = Observer()
        running = prompt_task(client, observer)
        async with asyncio.timeout(10):
            with pytest.raises(OmpCompletionTimeout, match="uncertain"):
                await running

    async def test_legitimate_background_beyond_the_budget_never_fires(
        self, client_factory: ClientFactory, short_guard: None
    ) -> None:
        client = await client_factory("yield")
        observer = Observer()
        running = prompt_task(client, observer)
        first_end = await observer.wait_for("agent_end")
        assert first_end.awaitingAsyncWork is True
        # Wait far past the guard budget: claiming background ownership must
        # keep the guard disarmed — the overall deadline remains the backstop.
        await asyncio.sleep(GUARD_S * 20)
        assert not running.done() and not running.cancelled()
        await client.request("release")
        async with asyncio.timeout(10):
            await running  # completes normally, no completion failure

    async def test_healthy_turn_finishes_far_inside_the_budget(
        self, client_factory: ClientFactory, short_guard: None
    ) -> None:
        client = await client_factory("basic")
        observer = Observer()
        async with asyncio.timeout(5):
            await prompt_task(client, observer)
        assert [event.type for event in observer.events][-1] == "agent_end"

    async def test_late_stale_result_cannot_complete_a_newer_turn(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("late-stale-result")
        observer = Observer()
        async with asyncio.timeout(5):
            await prompt_task(client, observer)
        await second_prompt(client)  # its result decoys with a stale id first


def process_is_gone(client: OmpRpcClient) -> bool:
    process = client._proc
    return process is None or process.returncode is not None
