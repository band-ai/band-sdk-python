"""Prove completion failure and recovery through native protocol barriers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.integrations.omprpc import (
    OmpCompletionTimeout,
    OmpProcessError,
    OmpRpcClient,
)
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeer import BACKGROUND_COMPLETE, COMPLETE, YIELDED
from tests.integrations.omprpc.kit import (
    BACKGROUND_WINDOW_S,
    CALLBACK_SETTLEMENT_BUDGET_S,
    COMPLETION_BUDGET_S,
    NO_CALLBACK_GRACE_S,
    BlockedEventObserver,
    ClientFactory,
    Observer,
    StubbornToolObserver,
    background_claim_observed,
    checkpoint_frames,
    completion_session,
    peer_process,
    process_is_alive,
    prompt_task,
    second_prompt,
    snapshots_observed,
    state_requests_observed,
)
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCompletionGuard:
    @pytest.mark.parametrize(
        "scenario",
        ["no-result", "pending-no-result", "stale-idle-result", "lost-settlement"],
    )
    async def test_idle_completion_failure_retires_before_explicit_restart(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        async with completion_session(client_factory, scenario) as session:
            client = session.client
            observer = Observer()
            async with session.clock.background(
                prompt_task(client, observer)
            ) as running:
                await observer.wait_for(OmpEventType.AGENT_END)
                if scenario == "pending-no-result":
                    await session.clock.elapse(BACKGROUND_WINDOW_S)
                    assert not running.done()
                    await client.request("release")
                await snapshots_observed(client)
                assert not running.done()

                await session.clock.elapse(COMPLETION_BUDGET_S)
                with pytest.raises(OmpProcessError):
                    await checkpoint_frames(client)

                with pytest.raises(OmpCompletionTimeout):
                    await session.clock.run_io(running)
            with pytest.raises(OmpProcessError):
                await client.request("get_state")
            assert not await asyncio.to_thread(process_is_alive, session.peer)

            await session.clock.run_io(client.start())
            replacement = await peer_process(client)
            assert replacement != session.peer
            await session.clock.run_io(second_prompt(client))

    async def test_settled_snapshot_with_pending_work_cannot_end_turn(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(
            client_factory, "settled-with-pending"
        ) as session:
            observer = Observer()
            async with session.clock.background(
                prompt_task(session.client, observer)
            ) as running:
                await observer.wait_for(OmpEventType.AGENT_END)
                await snapshots_observed(session.client)

                await session.clock.elapse(BACKGROUND_WINDOW_S)
                assert not running.done()

                await session.client.request("release")
                await session.clock.run_io(running)
            assert BACKGROUND_COMPLETE in observer.assistant_texts

    async def test_silent_background_completion_recovers_by_resnapshot(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(
            client_factory, "silent-settled-continuation"
        ) as session:
            observer = Observer()
            async with session.clock.background(
                prompt_task(session.client, observer)
            ) as running:
                await background_claim_observed(observer)
                await snapshots_observed(session.client)

                await session.clock.elapse(BACKGROUND_WINDOW_S)
                assert not running.done()

                await session.client.request("release")
                await session.clock.run_io(running)
            assert BACKGROUND_COMPLETE in observer.assistant_texts

    @pytest.mark.parametrize(
        "scenario", ["drain-blocked-event", "drain-blocked-background"]
    )
    async def test_blocked_event_drain_fails_after_settlement(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        async with completion_session(client_factory, scenario) as session:
            observer = BlockedEventObserver()
            async with session.clock.background(
                prompt_task(session.client, observer)
            ) as running:
                try:
                    await observer.entered.wait()
                    await snapshots_observed(session.client)
                    assert not running.done()
                    if scenario == "drain-blocked-background":
                        await session.clock.elapse(BACKGROUND_WINDOW_S)
                        assert not running.done()
                        await session.client.request("release")
                        await snapshots_observed(session.client, count=2)

                    await session.clock.elapse(COMPLETION_BUDGET_S)
                    with pytest.raises(OmpProcessError):
                        await checkpoint_frames(session.client)

                    with pytest.raises(OmpCompletionTimeout):
                        await session.clock.run_io(running)
                finally:
                    observer.resume.set()
            assert not await asyncio.to_thread(process_is_alive, session.peer)

    async def test_surviving_callback_blocks_restart_until_retirement_is_proven(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, "drain-stubborn") as session:
            client = session.client
            client.callback_exit_timeout_s = CALLBACK_SETTLEMENT_BUDGET_S
            observer = StubbornToolObserver()
            async with session.clock.background(
                prompt_task(client, observer)
            ) as running:
                try:
                    await observer.entered.wait()
                    await observer.wait_for(OmpEventType.AGENT_END)
                    await snapshots_observed(client)
                    assert not running.done()
                    # The callback already owns its settlement wait; subsequent
                    # cancellation checks must not grant it another grace period.
                    client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S

                    await session.clock.elapse(COMPLETION_BUDGET_S)
                    assert observer.cancel_seen.is_set()

                    with pytest.raises(OmpCompletionTimeout):
                        await session.clock.run_io(running)
                    assert not observer.finished.is_set()
                    assert not await asyncio.to_thread(process_is_alive, session.peer)
                    with pytest.raises(OmpProcessError):
                        await client.start()
                finally:
                    await observer.release()

            await observer.retired.wait()
            await session.clock.run_io(client.close())
            await session.clock.run_io(client.start())
            replacement = await peer_process(client)
            assert replacement != session.peer
            await session.clock.run_io(second_prompt(client))

    @pytest.mark.parametrize(
        ("scenario", "completion_barrier"),
        [
            ("no-result", snapshots_observed),
            ("lost-settlement", snapshots_observed),
            ("get-state-silent", state_requests_observed),
        ],
    )
    async def test_cancellation_during_completion_keeps_stop_semantics(
        self,
        client_factory: ClientFactory,
        scenario: str,
        completion_barrier: Callable[[OmpRpcClient], Awaitable[None]],
    ) -> None:
        async with completion_session(client_factory, scenario) as session:
            observer = Observer()
            async with session.clock.background(
                prompt_task(session.client, observer)
            ) as running:
                await observer.wait_for(OmpEventType.AGENT_END)
                await completion_barrier(session.client)
                assert not running.done()

                running.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await session.clock.run_io(running)
            with pytest.raises(OmpProcessError):
                await session.client.request("get_state")
            assert not await asyncio.to_thread(process_is_alive, session.peer)

    async def test_cancellation_with_surviving_callback_stays_cancellation(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, "drain-stubborn") as session:
            client = session.client
            client.callback_exit_timeout_s = CALLBACK_SETTLEMENT_BUDGET_S
            observer = StubbornToolObserver()
            async with session.clock.background(
                prompt_task(client, observer)
            ) as running:
                try:
                    await observer.entered.wait()
                    await observer.wait_for(OmpEventType.AGENT_END)
                    await snapshots_observed(client)
                    assert not running.done()
                    client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S

                    running.cancel()

                    with pytest.raises(asyncio.CancelledError):
                        await session.clock.run_io(running)
                    assert observer.cancel_seen.is_set()
                    assert not observer.finished.is_set()
                    assert not await asyncio.to_thread(process_is_alive, session.peer)
                    with pytest.raises(OmpProcessError):
                        await client.start()
                finally:
                    await observer.release()

            await observer.retired.wait()
            await session.clock.run_io(client.close())
            await session.clock.run_io(client.start())
            await session.clock.run_io(second_prompt(client))

    async def test_lost_snapshot_response_fails_at_the_request_layer(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, "get-state-silent") as session:
            observer = Observer()
            async with session.clock.background(
                prompt_task(session.client, observer)
            ) as running:
                await observer.wait_for(OmpEventType.AGENT_END)
                await state_requests_observed(session.client)
                assert not running.done()

                await session.clock.elapse(COMPLETION_BUDGET_S)
                with pytest.raises(OmpProcessError):
                    await checkpoint_frames(session.client)

                with pytest.raises(OmpCompletionTimeout):
                    await session.clock.run_io(running)
            with pytest.raises(OmpProcessError):
                await session.client.request("get_state")
            assert not await asyncio.to_thread(process_is_alive, session.peer)

    async def test_claimed_background_work_outlives_the_idle_budget(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, "yield") as session:
            observer = Observer()
            async with session.clock.background(
                prompt_task(session.client, observer)
            ) as running:
                first_end = await observer.wait_for(OmpEventType.AGENT_END)
                assert first_end.awaitingAsyncWork is True
                await snapshots_observed(session.client)

                await session.clock.elapse(BACKGROUND_WINDOW_S)
                assert not running.done()

                await session.client.request("release")
                await session.clock.run_io(running)
            assert observer.assistant_texts == [YIELDED, BACKGROUND_COMPLETE]

    async def test_healthy_turn_completes_and_keeps_the_transport_reusable(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, "basic") as session:
            observer = Observer()
            await session.clock.run_io(prompt_task(session.client, observer))
            assert observer.assistant_texts == [COMPLETE]
            assert observer.outline[-1] == OmpEventType.AGENT_END
            await session.clock.run_io(second_prompt(session.client))

    async def test_late_stale_result_cannot_complete_a_newer_turn(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, "late-stale-result") as session:
            observer = Observer()
            await session.clock.run_io(prompt_task(session.client, observer))
            assert observer.assistant_texts == [COMPLETE]
            await session.clock.run_io(second_prompt(session.client))
