"""A retired event worker exits when its cancellation-resistant callback returns."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError, OmpRpcError
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeer import LATE_PROMPT_ERROR
from tests.integrations.omprpc.kit import (
    NO_CALLBACK_GRACE_S,
    ClientFactory,
    StubbornEventObserver,
    completion_session,
    owned_rpc_process,
    peer_process,
    process_is_alive,
    prompt_task,
    second_prompt,
)
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestEventCallbackRetirement:
    @pytest.mark.parametrize("queued_event_count", [0, 1], ids=["empty", "backlog"])
    async def test_released_callback_proves_retirement_without_delivering_stale_events(
        self,
        client_factory: ClientFactory,
        queued_event_count: int,
    ) -> None:
        async with completion_session(client_factory, "event-backpressure") as session:
            client = session.client
            client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S
            process = owned_rpc_process(client)
            observer = StubbornEventObserver()
            async with session.clock.background(
                prompt_task(client, observer)
            ) as running:
                try:
                    await observer.entered.wait()
                    # The correlated response follows every burst event, proving
                    # the reader admitted this queue shape behind the held callback.
                    await client.request("event-burst", count=queued_event_count)
                    assert observer.outline == [OmpEventType.AGENT_START]
                    assert not running.done()

                    running.cancel()
                    await observer.cancel_seen.wait()
                    assert not observer.finished.is_set()
                    with pytest.raises(OmpProcessError):
                        await client.request("get_state")

                    with pytest.raises(asyncio.CancelledError):
                        await session.clock.run_io(running)
                    assert process.returncode is not None
                    assert not await asyncio.to_thread(process_is_alive, session.peer)
                    assert not observer.finished.is_set()
                    with pytest.raises(OmpProcessError):
                        await client.start()
                    with pytest.raises(OmpProcessError):
                        await session.clock.run_io(client.close())
                    assert owned_rpc_process(client) is process
                finally:
                    await observer.release()

            assert observer.finished.is_set()
            await observer.retired.wait()
            await session.clock.run_io(client.close())
            await session.clock.run_io(client.start())
            replacement = await peer_process(client)
            assert replacement != session.peer
            assert owned_rpc_process(client) is not process
            await session.clock.run_io(second_prompt(client))
            assert observer.outline == [OmpEventType.AGENT_START]

    async def test_rejected_prompt_keeps_its_surviving_callback_owned(
        self, client_factory: ClientFactory
    ) -> None:
        async with completion_session(client_factory, LATE_PROMPT_ERROR) as session:
            client = session.client
            client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S
            process = owned_rpc_process(client)
            observer = StubbornEventObserver()
            async with session.clock.background(
                prompt_task(client, observer)
            ) as running:
                try:
                    await observer.entered.wait()
                    await client.request("release")

                    with pytest.raises(OmpRpcError):
                        await session.clock.run_io(running)
                    assert observer.cancel_seen.is_set()
                    assert not observer.finished.is_set()

                    with pytest.raises(OmpProcessError):
                        await session.clock.run_io(client.close())
                    assert process.returncode is not None
                    assert not await asyncio.to_thread(process_is_alive, session.peer)
                    with pytest.raises(OmpProcessError):
                        await client.start()
                    assert not observer.finished.is_set()
                finally:
                    await observer.release()

            await observer.retired.wait()
            await session.clock.run_io(client.close())
            await session.clock.run_io(client.start())
            replacement = await peer_process(client)
            assert replacement != session.peer
            await session.clock.run_io(second_prompt(client))
