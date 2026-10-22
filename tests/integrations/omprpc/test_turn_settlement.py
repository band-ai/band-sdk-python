"""Turns await matching results and settlement across background work."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError, OmpPromptError
from band.integrations.omprpc.errors import CALLBACKS_UNFINISHED
from band.integrations.ompwire import (
    OmpEventType,
    OmpHostToolCall,
    OmpPromptStatus,
    OmpState,
    OmpToolResult,
)
from tests.integrations.omppeer import (
    BACKGROUND_COMPLETE,
    RESUMED_PROVIDER_FAILED,
    YIELDED,
)
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    owned_rpc_process,
    prompt_task,
    second_prompt,
)


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
            assert observer.assistant_texts == [YIELDED, BACKGROUND_COMPLETE]
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize(
        ("scenario", "error"),
        [
            ("resume-error", RESUMED_PROVIDER_FAILED),
            ("resume-error-compacted", RESUMED_PROVIDER_FAILED),
            (
                "resume-aborted",
                f"OMP continuation {OmpPromptStatus.ABORTED}",
            ),
        ],
    )
    async def test_resumed_failures_override_a_successful_initial_prompt_result(
        self, client_factory: ClientFactory, scenario: str, error: str
    ) -> None:
        client = await client_factory(scenario)
        observer = Observer()
        running = prompt_task(client, observer)
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request("release")
            async with asyncio.timeout(5):
                with pytest.raises(OmpPromptError, match=error):
                    await running
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
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory("settled-during-callback")
        client.callback_exit_timeout_s = 0
        process = owned_rpc_process(client)
        held = asyncio.Event()

        async def on_tool(_call: OmpHostToolCall) -> OmpToolResult:
            await held.wait()
            return OmpToolResult(content=[])

        observer = Observer()
        with pytest.raises(OmpProcessError, match=CALLBACKS_UNFINISHED):
            async with asyncio.timeout(5):
                await client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=on_tool,
                    on_ui=observer.on_ui,
                )
        held.set()
        assert process.returncode is not None
        await client.start()
        await second_prompt(client)
