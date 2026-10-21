"""MCP initialization stays owned by the room through cancellation and teardown."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from acp import RequestError

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.core.retention import Residency
from tests.adapters.waiting import spin, until
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.agent import AGENT_EXIT_FAILURE
from tests.integrations.acp.acp_toolkit.mcpresolver import (
    HeldResolver,
    Resolver,
    http_server,
    server_names,
)
from tests.mcpbackends import backends_created_by


class TestMcpSetup:
    """One setup serves waiting turns while retained custody owns its actual end."""

    @pytest.mark.asyncio
    async def test_concurrent_turns_share_resolution_and_rooms_are_independent(
        self,
    ) -> None:
        agent = FakeACPAgent(http=True)
        entered, release = asyncio.Event(), asyncio.Event()
        calls: list[str] = []

        async def resolver(workspace: str) -> list[Any]:
            calls.append(workspace)
            if workspace.endswith(DEFAULT_ROOM):
                entered.set()
                await release.wait()
            return [http_server()]

        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            first = asyncio.create_task(acp.send("a"))
            second = asyncio.create_task(acp.send("b"))
            try:
                await entered.wait()
                await acp.send("other room", room="room-2")
                assert not first.done() and not second.done()
            finally:
                release.set()
                await asyncio.gather(first, second)

        assert len(calls) == 2
        assert len(set(calls)) == 2
        assert len(agent.sessions) == 2

    @pytest.mark.asyncio
    async def test_one_waiter_leaving_does_not_cancel_the_shared_setup(self) -> None:
        agent = FakeACPAgent(http=True)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def resolver(workspace: str) -> list[Any]:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return [http_server()]

        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            leaving = asyncio.create_task(acp.send("a"))
            staying = asyncio.create_task(acp.send("b"))
            try:
                await entered.wait()
                await spin()
                leaving.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await leaving
            finally:
                release.set()
                await staying

        assert calls == 1
        assert len(agent.sessions) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("swallow", [False, True], ids=["cancelled", "swallowed"])
    async def test_last_waiter_leaving_cancels_setup_before_any_session(
        self, swallow: bool
    ) -> None:
        agent = FakeACPAgent(http=True)
        callback = HeldResolver(swallow=swallow)
        async with acp_adapter(agent, resolve_mcp_servers=callback) as acp:
            turn = asyncio.create_task(acp.send("a"))
            try:
                await callback.entered.wait()
                turn.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await turn
                await callback.settling.wait()
                assert agent.sessions == []
                assert acp.room().runtime is not None
            finally:
                callback.settle.set()
                await spin()
            await until(
                lambda: acp.room().session_setup.done(),
                failure="the abandoned setup never settled",
            )
            assert acp.room().session is None

        assert callback.cancellations == 1
        assert agent.sessions == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("swallow", [False, True], ids=["cancelled", "swallowed"])
    async def test_abandoned_setup_holds_the_room_until_it_settles(
        self, swallow: bool
    ) -> None:
        agent = FakeACPAgent(http=True).will_say("ok")
        callback = HeldResolver(swallow=swallow)
        async with acp_adapter(agent, resolve_mcp_servers=callback) as acp:
            abandoned = asyncio.create_task(acp.send("abandoned"))
            try:
                await callback.entered.wait()
                abandoned.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await abandoned
                await callback.settling.wait()
                follow_up = asyncio.create_task(acp.send("next"))
                await spin()
                assert len(callback.workspaces) == 1
                assert not follow_up.done()
            finally:
                callback.settle.set()
            reply = await follow_up

        assert reply.thoughts == ["ok"]
        assert callback.log == ["settled", "second call"]
        assert callback.cancellations == 1
        assert len(agent.sessions) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("self_cancel", [False, True])
    async def test_cleanup_joins_a_cancelling_setup_without_cancelling_again(
        self, self_cancel: bool
    ) -> None:
        agent = FakeACPAgent(http=True)
        callback = HeldResolver(self_cancel=self_cancel)
        config = fake_agent_config(turn_settle_timeout_s=0)
        async with acp_adapter(agent, config, resolve_mcp_servers=callback) as acp:
            turn = asyncio.create_task(acp.send("a"))
            try:
                await callback.entered.wait()
                if not self_cancel:
                    turn.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await turn
                await callback.settling.wait()
                exiting = agent.exits_slowly()
                cleanups = [
                    asyncio.create_task(acp.adapter.on_cleanup(DEFAULT_ROOM))
                    for _ in range(2)
                ]
                await spin()
                assert not exiting.received.is_set()
                assert acp.room().runtime is not None
                assert DEFAULT_ROOM in acp.adapter._workspaces.rooms
                assert callback.cancellations == 1
            finally:
                callback.settle.set()
            await exiting.received.wait()
            exiting.release.set()
            await asyncio.gather(*cleanups)
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert DEFAULT_ROOM not in acp.adapter._rooms
            assert DEFAULT_ROOM not in acp.adapter._workspaces.rooms

        assert callback.log == ["settled"]
        assert callback.cancellations == 1
        assert agent.sessions == []

    @pytest.mark.asyncio
    async def test_cleanup_waits_for_resolver_settlement_and_refuses_its_late_result(
        self,
    ) -> None:
        agent = FakeACPAgent(http=True)
        callback = HeldResolver()
        config = fake_agent_config(turn_settle_timeout_s=0)
        async with acp_adapter(agent, config, resolve_mcp_servers=callback) as acp:
            turn = asyncio.create_task(acp.send("a"))
            try:
                await callback.entered.wait()
                cleanup = asyncio.create_task(acp.adapter.on_cleanup(DEFAULT_ROOM))
                await callback.settling.wait()
                assert not cleanup.done()
                assert acp.room().runtime is not None
                assert DEFAULT_ROOM in acp.adapter._workspaces.rooms
            finally:
                callback.settle.set()
            await cleanup
            with pytest.raises(asyncio.CancelledError):
                await turn

        assert callback.cancellations == 1
        assert agent.sessions == []

    @pytest.mark.asyncio
    async def test_a_setup_cancelled_by_itself_never_reaches_the_agent(self) -> None:
        agent = FakeACPAgent(http=True)

        async def resolver(workspace: str) -> list[Any]:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            return [http_server()]

        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            with pytest.raises(asyncio.CancelledError):
                await acp.send("hello")

        assert agent.sessions == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("cleanup", "cancellation"),
        [
            ("leave", "none"),
            ("leave", "self"),
            ("stop", "none"),
            ("stop", "self"),
            ("cleanup_all", "none"),
            ("cleanup_all", "self"),
            ("cleanup_all", "shutdown"),
        ],
    )
    async def test_setup_requesting_teardown_does_not_wait_on_itself(
        self, cleanup: str, cancellation: str
    ) -> None:
        agent = FakeACPAgent(http=True).will_say("ok")
        config = fake_agent_config(inject_band_tools=True, turn_settle_timeout_s=0)
        callback = HeldResolver(
            self_cancel=cancellation == "self",
            wait_for_cancel=cancellation == "shutdown",
        )
        with backends_created_by():
            async with acp_adapter(agent, config, resolve_mcp_servers=callback) as acp:

                async def request() -> None:
                    with pytest.raises(CleanupIncompleteError, match="calling work"):
                        match cleanup:
                            case "leave":
                                await acp.adapter.on_cleanup(DEFAULT_ROOM)
                            case "stop":
                                await acp.adapter.stop()
                            case "cleanup_all":
                                await acp.adapter.cleanup_all()

                callback.request = request
                turn = asyncio.create_task(acp.send("a"))
                outside = None
                if cancellation == "shutdown":
                    await callback.entered.wait()
                    outside = asyncio.create_task(acp.adapter.cleanup_all())
                try:
                    async with asyncio.timeout(5):
                        await callback.settling.wait()
                    assert callback.log == ["request returned"]
                    assert acp.room().runtime is not None
                    assert DEFAULT_ROOM in acp.adapter._workspaces.rooms
                    assert acp.adapter._mcp.current is not None
                finally:
                    callback.settle.set()
                with pytest.raises(asyncio.CancelledError):
                    await turn
                if outside is not None:
                    await outside
                if cleanup == "leave":
                    await acp.adapter.on_cleanup(DEFAULT_ROOM)
                else:
                    await acp.adapter.cleanup_all()
                    with pytest.raises(RoomClosingError):
                        await acp.send("refused", room="room-2")
                    await acp.adapter.on_started("Fake Agent", "restarted")
                reply = await acp.send("after", room="room-2")

        assert reply.thoughts == ["ok"]
        assert callback.log == ["request returned", "settled", "second call"]
        assert callback.cancellations == int(cancellation != "none")
        assert len(agent.sessions) == 1

    @pytest.mark.asyncio
    async def test_callback_failure_never_falls_back_and_next_turn_recovers(
        self,
    ) -> None:
        agent = FakeACPAgent(http=True).will_say("ok")
        config = fake_agent_config(mcp_servers=[http_server(name="static")])
        resolver = Resolver(http_server(name="fixed"))
        fail = True

        async def flaky(workspace: str) -> list[Any]:
            if fail:
                raise LookupError("grant unavailable")
            return list(await resolver(workspace))

        async with acp_adapter(agent, config, resolve_mcp_servers=flaky) as acp:
            with pytest.raises(LookupError, match="grant unavailable"):
                await acp.send("first")
            failed_runtime = acp.room().runtime
            assert agent.sessions == []
            fail = False
            await acp.send("second")
            assert acp.runtime() is not failed_runtime

        assert [server_names(s["mcp_servers"]) for s in agent.sessions] == [["fixed"]]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["resolver", "session"])
    async def test_setup_failure_survives_failed_close_and_next_turn_recovers(
        self, failure: str
    ) -> None:
        agent = FakeACPAgent(http=True).will_say("recovered")
        if failure == "session":
            agent.fails_next_new_session()
        resolver = Resolver(http_server())

        async def callback(workspace: str) -> list[Any]:
            result = list(await resolver(workspace))
            if len(resolver.workspaces) == 1:
                agent.fails_next_exit()
                if failure == "resolver":
                    raise LookupError("resolver failed")
            return result

        async with acp_adapter(agent, resolve_mcp_servers=callback) as acp:
            with pytest.raises(LookupError if failure == "resolver" else RequestError):
                await acp.send("first")
            room = acp.room()
            await until(
                lambda: (
                    room.retained.snapshot.error is not None
                    and AGENT_EXIT_FAILURE in room.retained.snapshot.error
                ),
                failure="failed close was not retained",
            )
            failed_runtime = room.runtime
            assert failed_runtime is not None and not failed_runtime.closed
            assert room.retained.snapshot.state == Residency.CLEANUP_PENDING
            assert DEFAULT_ROOM in acp.adapter._workspaces.rooms

            reply = await acp.send("second")
            assert reply.thoughts == ["recovered"]
            assert acp.runtime() is not failed_runtime
            assert failed_runtime.closed

        assert len(resolver.workspaces) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failed_close", [False, True])
    async def test_a_captured_runtime_after_cleanup_starts_no_server_or_resolution(
        self, failed_close: bool
    ) -> None:
        agent = FakeACPAgent(http=True).will_say("ok")
        resolver = Resolver(http_server())
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by():
            async with acp_adapter(agent, config, resolve_mcp_servers=resolver) as acp:
                await acp.send("before")
                room, captured = acp.room(), acp.runtime()
                if failed_close:
                    agent.fails_next_exit()
                    with pytest.RaisesGroup(
                        pytest.RaisesExc(RuntimeError, match=AGENT_EXIT_FAILURE)
                    ):
                        await acp.adapter.cleanup_all()
                    assert room.runtime is captured
                    assert DEFAULT_ROOM in acp.adapter._workspaces.rooms
                    assert acp.adapter._mcp.current is not None
                else:
                    await acp.adapter.cleanup_all()
                    assert acp.adapter._mcp.current is None
                with pytest.raises(asyncio.CancelledError):
                    await acp.adapter._get_or_create_session(room, captured, None)
                assert len(resolver.workspaces) == 1
                assert len(agent.sessions) == 1
                await acp.adapter.cleanup_all()
                assert acp.adapter._mcp.current is None
                await acp.adapter.on_started("Fake Agent", "restarted")
                await acp.send("after")

        assert len(resolver.workspaces) == 2

    @pytest.mark.asyncio
    async def test_setup_abandoned_while_its_own_timeout_settles_is_not_reused(
        self,
    ) -> None:
        """The last waiter can leave while the callback is already cancelling
        itself; once the callback's timeout clears that request and it returns,
        its result must still not reach the agent or serve the next turn."""
        settling, release = asyncio.Event(), asyncio.Event()
        calls: list[str] = []

        async def resolver(workspace: str) -> list[Any]:
            calls.append(workspace)
            if len(calls) == 1:
                try:
                    async with asyncio.timeout(0):
                        try:
                            await asyncio.Event().wait()
                        finally:
                            settling.set()
                            await release.wait()
                except TimeoutError:
                    pass
            return [http_server(name=f"attempt-{len(calls)}")]

        agent = FakeACPAgent(http=True).will_say("ok")
        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            abandoned = asyncio.create_task(acp.send("abandoned"))
            try:
                await settling.wait()
                setup = acp.room().session_setup
                assert setup is not None and setup.cancelling()
                abandoned.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await abandoned
                release.set()
                await asyncio.wait({setup})
                reply = await acp.send("next")
            finally:
                release.set()
                await asyncio.gather(abandoned, return_exceptions=True)

        assert reply.thoughts == ["ok"]
        assert len(calls) == 2
        assert [server_names(s["mcp_servers"])[0] for s in agent.sessions] == [
            "attempt-2"
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("settling", [False, True], ids=["held", "own-timeout"])
    async def test_a_waiter_that_stays_reuses_the_shared_setup_result(
        self, settling: bool
    ) -> None:
        """When one of two waiters leaves, the shared setup is still the other's:
        even if the callback was cancelling its own timeout meanwhile, its result
        serves the remaining waiter instead of a second resolution and session."""
        entered, release = asyncio.Event(), asyncio.Event()
        calls: list[str] = []

        async def resolver(workspace: str) -> list[Any]:
            calls.append(workspace)
            if len(calls) == 1:
                if settling:
                    try:
                        async with asyncio.timeout(0):
                            try:
                                await asyncio.Event().wait()
                            finally:
                                entered.set()
                                await release.wait()
                    except TimeoutError:
                        pass
                else:
                    entered.set()
                    await release.wait()
            return [http_server(name=f"attempt-{len(calls)}")]

        agent = FakeACPAgent(http=True).will_say("ok")
        async with acp_adapter(agent, resolve_mcp_servers=resolver) as acp:
            first = asyncio.create_task(acp.send("first"))
            successor: asyncio.Task[Any] | None = None
            try:
                await entered.wait()
                room = acp.room()
                successor = asyncio.create_task(acp.send("successor"))
                await until(
                    lambda: room.session_waiters == 2, failure="successor never queued"
                )
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                release.set()
                reply = await successor
            finally:
                release.set()
                if successor is not None:
                    await asyncio.gather(successor, return_exceptions=True)
                await asyncio.gather(first, return_exceptions=True)

        assert reply.thoughts == ["ok"]
        assert len(calls) == 1
        assert len(agent.sessions) == 1
