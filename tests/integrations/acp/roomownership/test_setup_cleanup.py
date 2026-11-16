"""Shared setup cleanup follows the current caller through native prompting."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.integrations.acp.session_config import ACPConfigRequest
from tests.adapters.hold import Hold
from tests.adapters.waiting import until
from tests.core.retention.kit import SETTLE_GRACE_S, ManualClock
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.mcpresolver import HeldResolver
from tests.mcpbackends import backends_created_by
from tests.toolkit.tasks import background


class TestSetupCleanup:
    async def test_a_session_new_that_never_answers_fails_at_the_transition_bound(
        self, loop_clock: ManualClock
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        creating = agent.holds_next_new_session()
        config = fake_agent_config()
        async with (
            acp_adapter(agent, config) as acp,
            background(acp.send("hello")) as first,
        ):
            await creating.received.wait()
            room, unanswered = acp.room(), acp.runtime()
            setup = room.session_setup
            assert setup is not None
            await loop_clock.advance(config.retention.transition_timeout_s)
            with pytest.raises(TimeoutError):
                await first
            failed = acp.last_reply
            # The failed turn leaves the setup owned by the room, not forgotten.
            assert room.abandoned_setup is setup
            assert room.setup_observer is not None and room.setup_observer.abandoned
            assert agent.sessions == []

            served = await acp.send("again")

            assert failed.errors == [GENERIC_PROVIDER_FAILURE_MESSAGE]
            assert setup.done() and unanswered.closed
            assert acp.runtime() is not unanswered
            assert served.errors == []
        assert len(agent.sessions) == 1

    @pytest.mark.parametrize("cleanup", ["leave", "cleanup_all", "stop"])
    async def test_abandoned_settlement_dependency_survives_callback_cleanup(
        self, cleanup: str
    ) -> None:
        agent = FakeACPAgent(http=True)
        request = Hold()

        async def cleanup_from_callback() -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                match cleanup:
                    case "leave":
                        await acp.adapter.on_cleanup(DEFAULT_ROOM)
                    case "cleanup_all":
                        await acp.adapter.cleanup_all()
                    case "stop":
                        await acp.adapter.stop()

        callback = HeldResolver(
            swallow=True, wait_for_cancel=True, request=cleanup_from_callback
        )
        config = fake_agent_config(
            inject_band_tools=True, turn_settle_timeout_s=SETTLE_GRACE_S
        )
        with backends_created_by():
            async with acp_adapter(agent, config, resolve_mcp_servers=callback) as acp:
                clock = ManualClock()
                acp.adapter._retention.clock = clock
                first = asyncio.create_task(acp.send("first"))
                tasks = [first]
                try:
                    await callback.entered.wait()
                    room, runtime = acp.room(), acp.runtime()
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    await request.arrived()
                    successor = asyncio.create_task(acp.send("successor"))
                    tasks.append(successor)
                    await until(
                        lambda: bool(room.retained._waiting),
                        failure="successor not waiting for settlement",
                    )
                    unrelated = asyncio.create_task(acp.send("unrelated"))
                    tasks.append(unrelated)
                    await until(
                        lambda: room.session_waiters == 2, failure="sibling not queued"
                    )
                    request.released.set()
                    await callback.settling.wait()
                    await until(lambda: bool(clock.timers), failure="grace not armed")
                    await clock.advance(SETTLE_GRACE_S)
                    await until(unrelated.done, failure="sibling not cancelled")
                    assert unrelated.cancelled()
                    assert not successor.done()
                    assert not runtime.closed
                    assert DEFAULT_ROOM in acp.adapter._workspaces.rooms
                    assert acp.adapter._mcp.current is not None
                    callback.settle.set()
                    with pytest.raises(asyncio.CancelledError):
                        await successor
                    if cleanup == "leave":
                        await acp.adapter.on_cleanup(DEFAULT_ROOM)
                    else:
                        await acp.adapter.cleanup_all()
                    assert runtime.closed
                    assert DEFAULT_ROOM not in acp.adapter._workspaces.rooms
                    assert agent.sessions == []
                    assert callback.cancellations == 1
                finally:
                    request.released.set()
                    callback.settle.set()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.parametrize("cleanup", ["leave", "cleanup_all", "stop"])
    async def test_successor_finishes_prompt_beyond_cleanup_grace(
        self, cleanup: str
    ) -> None:
        agent = FakeACPAgent(http=True)
        prompting = Hold()

        @agent.on_prompt
        async def prompt(peer: FakeACPAgent, session_id: str) -> None:
            await prompting.wait()
            await peer.say(session_id, "finished")

        request, configured = Hold(), Hold()
        config = fake_agent_config(
            inject_band_tools=True, turn_settle_timeout_s=SETTLE_GRACE_S
        )

        async def configure(selection: ACPConfigRequest) -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                match cleanup:
                    case "leave":
                        await acp.adapter.on_cleanup(DEFAULT_ROOM)
                    case "cleanup_all":
                        await acp.adapter.cleanup_all()
                    case "stop":
                        await acp.adapter.stop()
            await configured.wait()

        with backends_created_by():
            async with acp_adapter(
                agent, config, resolve_session_config=configure
            ) as acp:
                clock = ManualClock()
                acp.adapter._retention.clock = clock
                first = asyncio.create_task(acp.send("first"))
                tasks = [first]
                try:
                    await request.arrived()
                    room, runtime = acp.room(), acp.runtime()
                    successor = asyncio.create_task(acp.send("successor"))
                    tasks.append(successor)
                    await until(
                        lambda: room.session_waiters == 2,
                        failure="successor not queued",
                    )
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    await until(
                        lambda: len(room.retained._waiting) == 1,
                        failure="successor did not join setup",
                    )
                    unrelated = asyncio.create_task(acp.send("unrelated"))
                    tasks.append(unrelated)
                    await until(
                        lambda: room.session_waiters == 2, failure="sibling not queued"
                    )
                    request.released.set()
                    await configured.arrived()
                    configured.released.set()
                    await prompting.arrived()
                    await clock.advance(SETTLE_GRACE_S)
                    await until(unrelated.done, failure="sibling not cancelled")
                    assert unrelated.cancelled()
                    assert not successor.done()
                    assert not runtime.closed
                    assert DEFAULT_ROOM in acp.adapter._workspaces.rooms
                    assert acp.adapter._mcp.current is not None
                    prompting.released.set()
                    reply = await successor
                    assert reply.thoughts == ["finished"]
                    assert agent.cancelled_sessions == []
                    if cleanup == "leave":
                        await acp.adapter.on_cleanup(DEFAULT_ROOM)
                    else:
                        await acp.adapter.cleanup_all()
                    assert runtime.closed
                    assert DEFAULT_ROOM not in acp.adapter._workspaces.rooms
                finally:
                    request.released.set()
                    configured.released.set()
                    prompting.released.set()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
