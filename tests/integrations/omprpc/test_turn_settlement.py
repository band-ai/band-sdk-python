"""Turns await matching results and settlement across background work."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations import omprpc
from band.integrations.omprpc import OmpProcessError, OmpPromptError
from band.integrations.ompwire import (
    OmpEventType,
    OmpHostToolCall,
    OmpState,
    OmpToolResult,
)
from tests.integrations.omprpc.kit import ClientFactory, Observer, prompt_task


class TestTurnSettlement:
    @pytest.mark.parametrize("scenario", ["yield", "stale", "false-result-settled"])
    async def test_initial_yield_never_finishes_a_background_turn(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            first_end = await observer.wait_for(OmpEventType.AGENT_END)
            assert first_end.awaitingAsyncWork is True
            await client.request("checkpoint")
            assert not running.done()
            await client.request("release")
            async with asyncio.timeout(5):
                await running
            assert observer.assistant_texts == ["yielded", "background complete"]
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize(
        "scenario", ["resume-error", "resume-error-compacted", "resume-aborted"]
    )
    async def test_resumed_failures_override_a_successful_initial_prompt_result(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request("release")
            expected = (
                "Resumed provider failed" if scenario != "resume-aborted" else "aborted"
            )
            async with asyncio.timeout(5):
                with pytest.raises(OmpPromptError, match=expected):
                    await running
            assert (
                sum(event.type == OmpEventType.AGENT_END for event in observer.events)
                == 2
            )
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_settlement_before_matching_result_waits_for_both(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("settlement-before-result")
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request("checkpoint")
            assert not running.done()
            await client.request("release")
            async with asyncio.timeout(5):
                await running
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize(
        "scenario", ["state-settles", "settles-during-state", "local"]
    )
    async def test_native_state_snapshot_and_subscribed_settlement_do_not_race(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        observer = Observer()
        async with asyncio.timeout(5):
            await prompt_task(client, observer)
        assert OmpState.model_validate(await client.request("get_state")).isSettled

    async def test_settlement_does_not_succeed_while_a_callback_is_still_running(
        self, client_factory: ClientFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(omprpc, "_CALLBACK_EXIT_TIMEOUT_S", 0)
        client = await client_factory("settled-during-callback")
        held = asyncio.Event()

        async def on_tool(_call: OmpHostToolCall) -> OmpToolResult:
            await held.wait()
            return OmpToolResult(content=[])

        observer = Observer()
        with pytest.raises(OmpProcessError, match="callbacks did not finish"):
            async with asyncio.timeout(5):
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=on_tool,
                    on_ui=observer.on_ui,
                )
        held.set()
