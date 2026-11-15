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
from tests.integrations.ompframes import RESUMED_PROVIDER_FAILED
from tests.integrations.omppeers.base import BACKGROUND_COMPLETE, COMPLETE, YIELDED
from tests.integrations.omprpc.events import (
    EventRecorder,
    follow_up_texts,
    record_prompt,
)
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.ompscenarios import PeerCommand, PeerScenario


class TestTurnSettlement:
    @pytest.mark.parametrize(
        "scenario",
        [
            PeerScenario.YIELD_TO_BACKGROUND,
            PeerScenario.STALE_COMPLETION_BEFORE_YIELD,
            PeerScenario.FALSE_RESULT_SETTLED,
        ],
    )
    async def test_initial_yield_never_finishes_a_background_turn(
        self, client_factory: ClientFactory, scenario: PeerScenario
    ) -> None:
        client = await client_factory(scenario)
        observer = EventRecorder()
        running = asyncio.create_task(record_prompt(client, observer))
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request(PeerCommand.CHECKPOINT)
            assert not running.done()
            await client.request(PeerCommand.RELEASE_PROMPT)
            async with asyncio.timeout(5):
                await running
            assert observer.assistant_texts == [YIELDED, BACKGROUND_COMPLETE]
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize(
        ("scenario", "error"),
        [
            (PeerScenario.RESUME_ERROR, RESUMED_PROVIDER_FAILED),
            (PeerScenario.RESUME_ERROR_COMPACTED, RESUMED_PROVIDER_FAILED),
            (
                PeerScenario.RESUME_ABORTED,
                f"OMP continuation {OmpPromptStatus.ABORTED}",
            ),
        ],
    )
    async def test_resumed_failures_override_a_successful_initial_prompt_result(
        self, client_factory: ClientFactory, scenario: PeerScenario, error: str
    ) -> None:
        client = await client_factory(scenario)
        observer = EventRecorder()
        running = asyncio.create_task(record_prompt(client, observer))
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request(PeerCommand.RELEASE_PROMPT)
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
        client = await client_factory(PeerScenario.SETTLEMENT_BEFORE_RESULT)
        observer = EventRecorder()
        running = asyncio.create_task(record_prompt(client, observer))
        try:
            await observer.wait_for(OmpEventType.AGENT_END)
            await client.request(PeerCommand.CHECKPOINT)
            assert not running.done()
            await client.request(PeerCommand.RELEASE_PROMPT)
            async with asyncio.timeout(5):
                await running
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize(
        "scenario",
        [
            PeerScenario.STATE_SETTLES,
            PeerScenario.SETTLES_DURING_STATE,
            PeerScenario.LOCAL_COMMAND,
        ],
    )
    async def test_native_state_snapshot_and_subscribed_settlement_do_not_race(
        self, client_factory: ClientFactory, scenario: PeerScenario
    ) -> None:
        client = await client_factory(scenario)
        observer = EventRecorder()
        async with asyncio.timeout(5):
            await record_prompt(client, observer)
        assert OmpState.model_validate(await client.request("get_state")).isSettled

    async def test_settlement_does_not_succeed_while_a_callback_is_still_running(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory(PeerScenario.SETTLED_DURING_CALLBACK)
        client.callback_exit_timeout_s = 0
        process = owned_rpc_process(client)
        held = asyncio.Event()

        async def on_tool(_call: OmpHostToolCall) -> OmpToolResult:
            await held.wait()
            return OmpToolResult(content=[])

        observer = EventRecorder()
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
        assert await follow_up_texts(client) == [COMPLETE]

    async def test_settlement_succeeds_after_the_running_callback_finishes(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory(PeerScenario.SETTLED_DURING_CALLBACK)
        started = asyncio.Event()
        release = asyncio.Event()

        async def on_tool(_call: OmpHostToolCall) -> OmpToolResult:
            started.set()
            await release.wait()
            return OmpToolResult(content=[])

        observer = EventRecorder()
        running = asyncio.create_task(
            client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=on_tool,
                on_ui=observer.on_ui,
            )
        )
        try:
            await started.wait()
            release.set()
            async with asyncio.timeout(5):
                await running
            assert owned_rpc_process(client).returncode is None
            assert await follow_up_texts(client) == [COMPLETE]
        finally:
            running.cancel()
            release.set()
            await asyncio.gather(running, return_exceptions=True)
