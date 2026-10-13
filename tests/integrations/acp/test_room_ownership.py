"""An ACP room owns its process, session and work until a release confirms it.

Each test drives a real adapter over real ACP JSON-RPC: an in-process fake
agent, or the stdio peer subprocess where the process boundary itself is under
test. Barriers hold the exact step a race needs; only a transition's own
time bound is ever waited out.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    nullcontext,
    suppress,
)
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError
from acp.schema import SessionConfigOptionSelect

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.client.streaming import ControlMode
from band.core.exceptions import (
    BandConfigError,
    CleanupIncompleteError,
    InputNotAccepted,
)
from band.core.model_catalog import ModelSelection
from band.core.retention import (
    ReleaseCause,
    ReleaseOutcome,
    Residency,
    RoomRetentionConfig,
)
from band.integrations.acp import client_adapter, client_runtime, sessions
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
    ACPTurnTimeoutError,
)
from band.integrations.acp.client_profiles import CURSOR_ASK_QUESTION_METHOD
from band.integrations.acp.client_types import ACPRoom
from band.integrations.acp.model_selection import MODEL_CATEGORY
from band.integrations.acp.session_config import ACPConfigError
from band.integrations.acp.sessions import ACPSessionRestoreError
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.conftest import eager_tasks, requires_eager_tasks
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    AcpSession,
    FakeACPAgent,
    Reply,
    accepted_work_finished,
    acp_adapter,
    decision_token,
    fake_agent_config,
    pending_decisions,
    released,
    select_option,
    started_acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import MODEL_OPTION_ID, Gate
from tests.integrations.acp.acp_toolkit.harness import (
    RELEASE_TURNS,
    SlowFailureReports,
    TranscriptTools,
    send_to,
    spin,
)
from tests.integrations.acp.acp_toolkit.peer import (
    EXIT_FAILURE,
    ExitStage,
    FailingExits,
    stdio_peer_config,
)
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by
from tests.mcpclient import crash_backend
from tests.paths import REPO_ROOT

CANCELLED = {"outcome": {"outcome": "cancelled"}}
# Start and transition bounds short enough that waiting them out costs nothing.
HUNG_START_TIMEOUT_S = 0.05
SHORT_TRANSITION_S = 0.05
# A guard on a wait that should finish at once, so a hang fails clearly.
TEST_GUARD_S = 5


def models(current: str, *offered: str) -> FakeACPAgent:
    """An agent with session load whose model select offers ``offered``."""
    return FakeACPAgent(
        supports_session_load=True,
        config_options=[model_select(current, *offered)],
    )


def model_select(current: str, *offered: str) -> SessionConfigOptionSelect:
    return select_option(MODEL_OPTION_ID, current, offered, category=MODEL_CATEGORY)


async def recover(session: AcpSession, room: str = DEFAULT_ROOM) -> ACPRoom:
    """Retire the room's process through its owner; the session is kept."""
    holder = session.room(room)
    assert holder.retained.recover(generation=holder.generation, reason="test")
    await released(holder)
    return holder


@asynccontextmanager
async def woken(adapter: ACPClientAdapter, room: ACPRoom) -> AsyncIterator[None]:
    """Acquire ``room`` as a deliberate wake does, then close that process.

    Idle release is disabled for every ACP profile, so the wake path is driven
    through the adapter's acquisition callback directly.
    """
    try:
        await adapter._acquire_room(room, room.generation + 1, True)
        yield
    finally:
        await adapter._release_room(room, ReleaseCause.RECOVERY)


async def question_asked(reply: Reply) -> None:
    """Let a running turn post its question decision to ``reply``'s room."""
    for _ in range(RELEASE_TURNS):
        if any("Cursor needs input" in text for text in reply.texts):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"no question was asked: {reply.texts}")


def question_token(reply: Reply) -> str:
    """The token of the one question decision ``reply`` asked the room."""
    [prompt] = [text for text in reply.texts if "Cursor needs input" in text]
    return decision_token(prompt)


class TestDeliberateWake:
    """A wake restores the retained session exactly or fails keeping it."""

    @pytest.mark.asyncio
    async def test_reloads_the_exact_session_with_fresh_mcp_and_selection(
        self,
    ) -> None:
        agent = models("a", "a", "b").will_say("ok")
        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("hello")
            retained = session.session_id(DEFAULT_ROOM)
            await session.adapter.apply_model_selection(
                ModelSelection(model="b"), room_id=DEFAULT_ROOM
            )
            stale_url = agent.mcp_server(retained, BAND_MCP_SERVER_NAME).url
            room = await recover(session)
            await crash_backend(session.adapter._mcp)
            agent.knows_session(retained).advertises(model_select("a", "a", "b"))
            switched = len(agent.config_selections(retained))

            async with woken(session.adapter, room):
                assert room.live_session is not None
                assert room.live_session[0] == retained
                live_url = agent.mcp_server(retained, BAND_MCP_SERVER_NAME).url

        assert agent.session_load_requests == [retained]
        assert agent.session_ids() == [retained]
        assert agent.config_selections(retained)[switched:] == [(MODEL_OPTION_ID, "b")]
        assert live_url != stale_url

    @pytest.mark.asyncio
    @pytest.mark.parametrize("loadable", [True, False], ids=["refused", "no-load"])
    async def test_a_refused_load_keeps_the_identity_and_never_falls_back(
        self, loadable: bool
    ) -> None:
        agent = FakeACPAgent(supports_session_load=loadable).will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            retained = session.session_id(DEFAULT_ROOM)
            room = await recover(session)

            with pytest.raises(ACPSessionRestoreError, match=retained):
                async with woken(session.adapter, room):
                    pass

            assert room.session is not None
            assert (room.session.session_id, room.session.live) == (retained, False)
        assert agent.session_ids() == [retained]
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_an_unanswered_load_fails_at_its_bound_keeping_the_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sessions, "ACP_SESSION_LOAD_TIMEOUT_SECONDS", 0.05)
        agent = FakeACPAgent(supports_session_load=True).will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            retained = session.session_id(DEFAULT_ROOM)
            room = await recover(session)
            loading = agent.knows_session(retained).holds_next_session_load()
            try:
                with pytest.raises(ACPSessionRestoreError, match="no answer"):
                    async with woken(session.adapter, room):
                        pass
            finally:
                loading.release.set()

            assert room.session is not None
            assert (room.session.session_id, room.session.live) == (retained, False)
        assert agent.session_ids() == [retained]

    @pytest.mark.asyncio
    async def test_a_refused_selection_is_kept_for_the_room(self) -> None:
        agent = models("a", "a", "b").will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            await session.adapter.apply_model_selection(
                ModelSelection(model="b"), room_id=DEFAULT_ROOM
            )
            retained = session.session_id(DEFAULT_ROOM)
            room = await recover(session)
            agent.knows_session(retained).advertises(model_select("a", "a"))

            with pytest.raises(ACPConfigError):
                async with woken(session.adapter, room):
                    pass

            assert session.adapter._room_selection(DEFAULT_ROOM) == ModelSelection(
                model="b"
            )
            assert room.session is not None
            assert room.session.session_id == retained


class TestRecovery:
    """A failed turn retires its process through the owner and keeps the session."""

    @pytest.mark.asyncio
    async def test_an_idle_release_is_refused_and_keeps_the_process(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            room = session.room()
            runtime = session.runtime()

            outcome = await session.adapter._release_room(room, ReleaseCause.IDLE)

            assert outcome == ReleaseOutcome.BUSY
            assert room.runtime is runtime and not runtime.closed
            assert room.live_session == (session.session_id(DEFAULT_ROOM), runtime)

    @pytest.mark.asyncio
    async def test_the_next_turn_reloads_the_recovered_session(self) -> None:
        agent = FakeACPAgent(supports_session_load=True)
        prompts = 0

        @agent.on_prompt
        async def fail_first(peer: FakeACPAgent, session_id: str) -> None:
            nonlocal prompts
            prompts += 1
            if prompts == 1:
                raise RequestError.internal_error({"detail": "process wedged"})

        async with acp_adapter(agent) as session:
            with pytest.raises(RequestError):
                await session.send("hello")
            retained = session.session_id(DEFAULT_ROOM)
            first_runtime = session.room().runtime
            agent.knows_session(retained)

            await session.send("again")

            assert session.runtime() is not first_runtime
        assert agent.session_load_requests == [retained]
        assert agent.session_ids() == [retained]

    @pytest.mark.asyncio
    async def test_a_refused_selection_after_a_crash_fails_one_turn_and_reverts(
        self,
    ) -> None:
        agent = models("a", "a", "b").will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            await session.adapter.apply_model_selection(
                ModelSelection(model="b"), room_id=DEFAULT_ROOM
            )
            retained = session.session_id(DEFAULT_ROOM)
            await recover(session)
            agent.knows_session(retained).advertises(model_select("a", "a"))

            refused = await session.send("again")
            refused_on = session.room().runtime
            served = await session.send("once more")

            assert refused.errors == [
                (
                    'ACP session configuration failed: model "b" is not advertised; '
                    "available: a"
                )
            ]
            assert served.errors == []
            # Loaded but unconfigured, the session is reloaded on a new process.
            assert session.runtime() is not refused_on
            assert (
                session.adapter._room_selection(DEFAULT_ROOM)
                == session.adapter.model_selection
            )
        assert [prompt["session_id"] for prompt in agent.prompts] == [retained] * 2
        assert retained not in agent.closed_sessions

    @pytest.mark.asyncio
    async def test_a_failed_session_setup_reports_and_recovers_the_room(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok").fails_next_new_session()
        async with acp_adapter(agent) as session:
            with pytest.raises(RequestError):
                await session.send("hello")
            failed = session.last_reply
            first_runtime = session.room().runtime

            await session.send("again")

            assert failed.errors == ["Internal error"]
            assert session.runtime() is not first_runtime
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_a_superseded_generation_cannot_recover_its_successor(
        self,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True).will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            room = session.room()
            superseded = room.generation
            agent.knows_session(session.session_id(DEFAULT_ROOM))
            await recover(session)
            await session.send("again")
            successor = session.runtime()

            assert not room.retained.recover(generation=superseded, reason="late")
            assert room.retained.snapshot.state == Residency.RESIDENT
            assert session.runtime() is successor and not successor.closed

    @pytest.mark.asyncio
    async def test_tools_are_bound_before_the_recovered_session_restores(
        self,
    ) -> None:
        """A Band tool the agent calls while restoring is served with the
        restoring turn's tools, not the failed turn's."""
        reply = "restored"

        class RestoringAgent(FakeACPAgent):
            async def load_session(self, cwd, session_id, mcp_servers=None, **kwargs):
                loaded = await super().load_session(
                    cwd, session_id, mcp_servers, **kwargs
                )
                await self.call_mcp_tool(
                    session_id=session_id,
                    server=BAND_MCP_SERVER_NAME,
                    tool_name=BandTool.SEND_MESSAGE,
                    arguments={"content": reply, "mentions": ["@peer-1"]},
                )
                return loaded

        agent = RestoringAgent(supports_session_load=True)
        prompts = 0

        @agent.on_prompt
        async def fail_first(peer: FakeACPAgent, session_id: str) -> None:
            nonlocal prompts
            prompts += 1
            if prompts == 1:
                raise RequestError.internal_error()

        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            with pytest.raises(RequestError):
                await session.send("hello")
            failed = session.last_reply
            agent.knows_session(session.session_id(DEFAULT_ROOM))

            restoring = await session.send("again")

        assert restoring.texts == [reply]
        assert reply not in failed.texts


class TestFinalCleanup:
    """Leaving keeps the room owned until its process is confirmed closed."""

    @pytest.mark.asyncio
    async def test_a_failed_exit_keeps_the_room_owned_until_a_retry_succeeds(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            runtime = session.runtime()
            agent.fails_next_exit()

            with pytest.raises(RuntimeError, match="agent exit failed"):
                await session.adapter.on_cleanup(DEFAULT_ROOM)

            room = session.room()
            assert room.runtime is runtime
            assert room.retained.snapshot.pending_cleanup
            with pytest.raises(InputNotAccepted, match="closing"):
                await session.send("rejoin")
            assert agent.session_ids() == ["fake-session-1"]

            await session.adapter.on_cleanup(DEFAULT_ROOM)

            assert runtime.closed
            assert DEFAULT_ROOM not in session.adapter._rooms
            await session.send("rejoined")
        assert agent.session_ids() == ["fake-session-1", "fake-session-2"]

    @pytest.mark.asyncio
    async def test_a_cancelled_cleanup_keeps_closing_and_refuses_a_rejoin(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        exiting = agent.exits_slowly()
        async with acp_adapter(agent) as session:
            await session.send("hello")
            runtime = session.runtime()
            cleanup = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await exiting.received.wait()

            with pytest.raises(InputNotAccepted, match="closing"):
                await session.send("rejoin")
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            assert not runtime.closed
            assert DEFAULT_ROOM in session.adapter._rooms

            exiting.release.set()
            await session.adapter.on_cleanup(DEFAULT_ROOM)

            assert runtime.closed
            assert DEFAULT_ROOM not in session.adapter._rooms

    @pytest.mark.asyncio
    async def test_a_held_room_keeps_the_shared_mcp_server_until_a_retry(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        config = stop_budget()
        with backends_created_by():
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()
                exiting = agent.exits_slowly()

                with pytest.raises(CleanupIncompleteError):
                    await session.adapter.cleanup_all()
                assert session.adapter._mcp.current is not None
                assert not runtime.closed
                # A restart retries the unfinished stop; it is still held.
                with pytest.raises(CleanupIncompleteError, match="still running"):
                    await session.adapter.on_started("Fake Agent", "too early")
                assert DEFAULT_ROOM in session.adapter._rooms
                assert not runtime.closed

                exiting.release.set()
                await session.adapter.cleanup_all()

                assert runtime.closed
                assert session.adapter._mcp.current is None
                await session.adapter.on_started("Fake Agent", "restarted")

    @pytest.mark.asyncio
    async def test_an_mcp_stop_past_the_budget_is_left_for_a_retry(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopping = asyncio.Event()
        backend = FakeBandMCPBackend(stop_release=stopping)
        config = stop_budget()
        with backends_created_by(backend):
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()

                with pytest.raises(CleanupIncompleteError, match="MCP backend"):
                    await session.adapter.cleanup_all()
                assert runtime.closed

                stopping.set()
                await session.adapter.cleanup_all()

                assert session.adapter._mcp.current is None
                assert backend.stop_calls == 1

    @pytest.mark.asyncio
    async def test_a_failed_room_exit_fails_shutdown_and_keeps_the_mcp_server(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by():
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()
                agent.fails_next_exit()

                with pytest.RaisesGroup(
                    pytest.RaisesExc(RuntimeError, match="agent exit failed")
                ):
                    await session.adapter.cleanup_all()
                assert session.adapter._mcp.current is not None
                assert not runtime.closed

                await session.adapter.cleanup_all()

                assert runtime.closed
                assert session.adapter._mcp.current is None

    @pytest.mark.asyncio
    async def test_retired_session_closes_finish_before_the_process_exits(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("before the crash")
            retired = session.session_id(DEFAULT_ROOM)
            closing = agent.holds_session_closes()
            exiting = agent.exits_slowly()
            await crash_backend(session.adapter._mcp)
            await session.send("after the crash")
            await closing.received.wait()

            cleanup = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await spin()
            exited_under_a_held_close = exiting.received.is_set()
            closing.release.set()
            await exiting.received.wait()

            assert not exited_under_a_held_close
            assert agent.closed_sessions == [retired]
            exiting.release.set()
            await cleanup


def stop_budget() -> ACPClientAdapterConfig:
    """Band tools on, with a transition bound short enough to outlast."""
    return fake_agent_config(
        inject_band_tools=True,
        retention=RoomRetentionConfig(transition_timeout_s=SHORT_TRANSITION_S),
    )


class TestDirectStop:
    """A direct stop owns the MCP server's close until it is confirmed, then
    later turns start afresh."""

    @pytest.mark.asyncio
    async def test_a_cancelled_stop_keeps_closing_the_server_until_a_retry(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopped, closing = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=stopped)
        with backends_created_by(backend) as starts:
            async with acp_adapter(agent, stop_budget()) as session:
                await session.send("hello")
                stopping = asyncio.create_task(session.adapter.stop())
                await closing.wait()
                stopping.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await stopping

                with pytest.raises(CleanupIncompleteError, match="MCP backend"):
                    await session.adapter.stop()
                assert len(starts.requested) == 1

                stopped.set()
                await session.adapter.stop()
                reply = await session.send("again")

        assert backend.stop_calls == 1
        assert len(starts.requested) == 2
        assert reply.errors == []

    @pytest.mark.asyncio
    async def test_a_failed_close_is_retried_by_the_next_stop(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        backend = FakeBandMCPBackend()
        backend.fail_stops = 1
        with backends_created_by(backend) as starts:
            async with acp_adapter(agent, stop_budget()) as session:
                await session.send("hello")
                with pytest.raises(OSError, match="stop failed"):
                    await session.adapter.stop()

                await session.adapter.stop()
                reply = await session.send("again")

        assert backend.stop_calls == 2
        assert len(starts.requested) == 2
        assert reply.errors == []

    @pytest.mark.asyncio
    async def test_concurrent_stops_share_one_close(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopped, closing = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=stopped)
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by(backend):
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                stops = [asyncio.create_task(session.adapter.stop()) for _ in range(2)]
                await closing.wait()
                stopped.set()
                async with asyncio.timeout(TEST_GUARD_S):
                    await asyncio.gather(*stops)
                reply = await session.send("again")

        assert backend.stop_calls == 1
        assert reply.errors == []


class TestIndependentRooms:
    """One room's held start or exit never holds another room's turn."""

    @pytest.mark.asyncio
    async def test_a_held_start_does_not_hold_another_room(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            starting = agent.holds_next_initialize()
            held = asyncio.create_task(session.send("hello", room="room-a"))
            await starting.received.wait()

            await session.send("hello", room="room-b")

            assert not held.done()
            assert session.room("room-b").live_session is not None
            starting.release.set()
            await held

    @pytest.mark.asyncio
    async def test_a_held_exit_does_not_hold_another_room(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello", room="room-a")
            await session.send("hello", room="room-b")
            exiting = agent.exits_slowly()
            cleanup = asyncio.create_task(session.adapter.on_cleanup("room-a"))
            await exiting.received.wait()

            await session.send("again", room="room-b")

            assert not cleanup.done()
            exiting.release.set()
            await cleanup


def holds_prompt(agent: FakeACPAgent, number: int) -> Gate:
    """Hold the agent's ``number``-th prompt until release; others answer."""
    prompting = Gate()

    @agent.on_prompt
    async def hold(peer: FakeACPAgent, session_id: str) -> None:
        if len(peer.prompts) == number:
            await prompting.hold()

    return prompting


class TestSettings:
    """A model switch takes its room in turn, like a prompt."""

    @pytest.mark.asyncio
    async def test_a_switch_on_a_recovered_room_starts_no_process(self) -> None:
        agent = models("a", "a", "b")
        async with acp_adapter(agent) as session:
            await session.send("bootstrap")
            session_id = session.session_id(DEFAULT_ROOM)
            room = await recover(session)

            with pytest.raises(BandConfigError, match="no live ACP session"):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )

            assert room.runtime is None
        assert agent.config_selections(session_id) == []

    @pytest.mark.asyncio
    async def test_a_cancelled_switch_caller_stays_cancelled_while_its_room_leaves(
        self,
    ) -> None:
        agent = models("a", "a", "b")
        prompting = holds_prompt(agent, 2)
        async with acp_adapter(agent) as session:
            await session.send("bootstrap")
            session_id = session.session_id(DEFAULT_ROOM)
            held = asyncio.create_task(session.send("held"))
            await prompting.received.wait()
            switch = asyncio.create_task(
                session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )
            )
            await spin()

            async with asyncio.timeout(TEST_GUARD_S):
                held.cancel()
                leaving = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
                await asyncio.sleep(0)
                # The caller gives up as the room starts closing.
                assert session.room().closing
                switch.cancel()
                prompting.release.set()
                await leaving
                with pytest.raises(asyncio.CancelledError):
                    await switch
                await asyncio.gather(held, return_exceptions=True)

        assert agent.config_selections(session_id) == []

    @pytest.mark.asyncio
    async def test_a_switch_waits_for_the_rooms_running_prompt(self) -> None:
        agent = models("a", "a", "b")
        prompting = holds_prompt(agent, 2)
        async with acp_adapter(agent) as session:
            await session.send("bootstrap")
            session_id = session.session_id(DEFAULT_ROOM)
            held = asyncio.create_task(session.send("held"))
            await prompting.received.wait()
            switch = asyncio.create_task(
                session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )
            )
            await spin()

            assert not switch.done()
            assert agent.config_selections(session_id) == []

            prompting.release.set()
            await held
            await switch

        assert agent.config_selections(session_id) == [(MODEL_OPTION_ID, "b")]

    @pytest.mark.asyncio
    async def test_a_switch_waiting_for_a_prompt_never_holds_the_rooms_leave(
        self,
    ) -> None:
        agent = models("a", "a", "b")
        prompting = holds_prompt(agent, 2)
        async with acp_adapter(agent) as session:
            await session.send("bootstrap")
            session_id = session.session_id(DEFAULT_ROOM)
            held = asyncio.create_task(session.send("held"))
            await prompting.received.wait()
            switch = asyncio.create_task(
                session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id=DEFAULT_ROOM
                )
            )
            await spin()

            async with asyncio.timeout(TEST_GUARD_S):
                # The runtime cancels the room's handler, then the room leaves.
                held.cancel()
                leaving = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
                while session_id not in agent.cancelled_sessions:
                    await asyncio.sleep(0)
                # The agent honours the cancel, ending its prompt.
                prompting.release.set()
                await leaving
                with pytest.raises(BandConfigError, match="ended mid-switch"):
                    await switch
                await asyncio.gather(held, return_exceptions=True)

        assert agent.config_selections(session_id) == []

    @pytest.mark.asyncio
    async def test_a_prompt_in_one_room_never_holds_another_rooms_switch(
        self,
    ) -> None:
        agent = models("a", "a", "b")
        prompting = holds_prompt(agent, 3)
        async with acp_adapter(agent) as session:
            await session.send("bootstrap", room="room-a")
            await session.send("bootstrap", room="room-b")
            other = session.session_id("room-b")
            held = asyncio.create_task(session.send("held", room="room-a"))
            await prompting.received.wait()

            async with asyncio.timeout(TEST_GUARD_S):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="b"), room_id="room-b"
                )

            assert agent.config_selections(other) == [(MODEL_OPTION_ID, "b")]
            prompting.release.set()
            await held


class QuestioningAgent(FakeACPAgent):
    """Asks one Cursor question per prompt and records each session's answer."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: dict[str, dict[str, object]] = {}
        self.on_prompt(self.ask_mode)

    async def ask_mode(self, peer: FakeACPAgent, session_id: str) -> None:
        self.answers[session_id] = await self.ask_cursor_question(
            session_id, "mode", "agent", "plan"
        )


@asynccontextmanager
async def cursor(agent: FakeACPAgent, **settings: Any) -> AsyncIterator[AcpSession]:
    adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent", **settings))
    async with started_acp_adapter(adapter, agent) as session:
        yield session


@asynccontextmanager
async def inline(agent: FakeACPAgent, **settings: Any) -> AsyncIterator[AcpSession]:
    """An adapter whose turns run inside their message handler (OMP, Copilot)."""
    async with acp_adapter(agent, fake_agent_config(**settings)) as session:
        yield session


async def stop(session: AcpSession, handler: asyncio.Task[Reply]) -> None:
    """A room ``/stop``: the runtime cancels the handler and interrupts the room."""
    handler.cancel()
    await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.STOP)
    with suppress(asyncio.CancelledError):
        await handler


def answered(option: str) -> dict[str, object]:
    return {
        "outcome": {
            "outcome": "answered",
            "answers": [{"questionId": "mode", "selectedOptionIds": [option]}],
        }
    }


class TestStartup:
    """A process starts within the room's bounded acquisition."""

    @pytest.mark.asyncio
    async def test_a_start_that_never_answers_fails_at_its_bound_and_recovers(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        agent.holds_next_initialize()
        config = fake_agent_config(start_timeout_s=HUNG_START_TIMEOUT_S)
        async with acp_adapter(agent, config) as session:
            with pytest.raises(TimeoutError, match="did not start"):
                await session.send("hello")

            served = await session.send("again")

            assert served.errors == []
            assert session.room().generation == 2
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("opened", [inline, cursor], ids=["inline", "cursor"])
    async def test_a_stop_during_a_start_that_never_answers_frees_the_room(
        self, opened: Callable[..., AbstractAsyncContextManager[AcpSession]]
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        starting = agent.holds_next_initialize()
        # Far above the bound below: the stop, not the start bound, frees it.
        async with opened(agent, start_timeout_s=60) as session:
            first = asyncio.create_task(session.send("hello"))
            await starting.received.wait()

            await stop(session, first)
            async with asyncio.timeout(TEST_GUARD_S):
                served = await session.send("again")
                await accepted_work_finished(session.room())

            assert served.errors == []
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_a_start_outlasting_the_transition_timeout_fails_and_recovers(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        starting = agent.holds_next_initialize()
        config = fake_agent_config(
            retention=RoomRetentionConfig(transition_timeout_s=SHORT_TRANSITION_S)
        )
        async with acp_adapter(agent, config) as session:
            with pytest.raises(TimeoutError):
                await session.send("hello")
            starting.release.set()
            await released(session.room())

            served = await session.send("again")

            assert served.errors == []
            assert session.room().generation == 2
        assert len(agent.prompts) == 1


class TestCursorOrigin:
    """Cursor's sessionless requests resolve against the process that sent them."""

    @pytest.mark.asyncio
    async def test_two_rooms_park_and_resolve_their_own_questions(self) -> None:
        agent = QuestioningAgent()
        async with cursor(agent) as session:
            room_a = await session.send("ask", room="room-a")
            room_b = await session.send("ask", room="room-b")

            await session.send(
                f"/cursor answer {question_token(room_b)} mode=plan", room="room-b"
            )
            await session.send(
                f"/cursor answer {question_token(room_a)} mode=agent", room="room-a"
            )
            for room in ("room-a", "room-b"):
                await accepted_work_finished(session.room(room))

            assert agent.answers == {
                session.session_id("room-a"): answered("agent"),
                session.session_id("room-b"): answered("plan"),
            }

    @pytest.mark.asyncio
    async def test_a_parked_question_pins_its_process_and_stays_answerable(
        self,
    ) -> None:
        agent = QuestioningAgent()
        async with cursor(agent) as session:
            asked = await session.send("ask")
            room = session.room()

            assert room.retained.snapshot.leases == 1
            assert room.retained.snapshot.accepted == 1
            assert not session.runtime().closed

            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await accepted_work_finished(room)

            assert room.retained.snapshot.leases == 0
            assert agent.answers == {session.session_id(DEFAULT_ROOM): answered("plan")}

    @pytest.mark.asyncio
    async def test_an_old_process_cannot_open_a_decision_on_its_successor(
        self,
    ) -> None:
        agent = FakeACPAgent()
        parked = Gate()
        prompts = 0

        @agent.on_prompt
        async def park_second(peer: FakeACPAgent, session_id: str) -> None:
            nonlocal prompts
            prompts += 1
            if prompts == 2:
                parked.received.set()
                await parked.release.wait()

        async with cursor(agent) as session:
            await session.send("hello")
            old_client = session.runtime().client
            assert old_client is not None
            await recover(session)
            successor = asyncio.create_task(session.send("again"))
            await parked.received.wait()

            async with asyncio.timeout(TEST_GUARD_S):
                stale = await old_client.ext_method(
                    CURSOR_ASK_QUESTION_METHOD,
                    {"questions": [{"id": "mode", "options": [{"id": "agent"}]}]},
                )

            assert stale == CANCELLED
            assert pending_decisions(session.adapter) == {}
            parked.release.set()
            await successor
            assert session.last_reply.texts == []

    @pytest.mark.asyncio
    async def test_a_sessionless_update_lands_in_its_rooms_open_turn(self) -> None:
        agent = FakeACPAgent().will_update_cursor_todos("ship it", sessionless=True)
        async with cursor(agent) as session:
            reply = await session.send("hello")

        assert reply.plans == ["- [ ] ship it"]


class TestCursorTurns:
    """Every accepted Cursor turn is reachable by its room's controls."""

    @pytest.mark.asyncio
    async def test_an_interrupt_during_startup_ends_the_turn_and_frees_the_room(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        starting = agent.holds_next_initialize()
        async with cursor(agent) as session:
            turn = asyncio.create_task(session.send("hello"))
            await starting.received.wait()

            await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.STOP)
            starting.release.set()

            with pytest.raises(asyncio.CancelledError):
                await turn
            assert agent.prompts == []

            served = await session.send("again")
            await accepted_work_finished(session.room())

            assert served.errors == []
        assert len(agent.prompts) == 1

    @pytest.mark.asyncio
    async def test_an_interrupt_before_any_decision_cancels_the_prompt(
        self,
    ) -> None:
        agent = FakeACPAgent()
        prompting = Gate()

        @agent.on_prompt
        async def hold(peer: FakeACPAgent, session_id: str) -> None:
            prompting.received.set()
            await prompting.release.wait()

        async with cursor(agent) as session:
            turn = asyncio.create_task(session.send("hello"))
            await prompting.received.wait()

            await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.INTERRUPT)

            with pytest.raises(asyncio.CancelledError):
                await turn
            assert agent.cancelled_sessions == [session.session_id(DEFAULT_ROOM)]
            prompting.release.set()

    @pytest.mark.asyncio
    async def test_a_message_during_a_parked_turn_is_left_for_a_retry(
        self,
    ) -> None:
        """The host keeps it, so the parked turn's answer still gets through
        and the message runs once the room is free."""
        agent = FakeACPAgent()
        answers: list[dict[str, object]] = []

        @agent.on_prompt
        async def ask_first(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                answers.append(
                    await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                )

        async with cursor(agent) as session:
            asked = await session.send("ask")
            refused = TranscriptTools()
            with pytest.raises(InputNotAccepted):
                await session.send("next", tools=refused)
            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await accepted_work_finished(session.room())
            retried = await session.send("next")

        assert refused.reply.texts == [] and refused.reply.errors == []
        assert answers == [answered("plan")]
        assert len(agent.prompts) == 2 and retried.errors == []

    @pytest.mark.asyncio
    async def test_a_message_during_an_interrupted_turns_unwind_is_not_failed(
        self,
    ) -> None:
        """Cursor may still be finishing a cancelled prompt when the next one
        arrives; its ``session_busy`` refusal leaves the message for a retry."""
        agent = FakeACPAgent()
        cancelled = asyncio.Event()

        @agent.on_prompt
        async def busy_after_cancel(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                await cancelled.wait()
                return
            raise RequestError(-32003, "busy", {"reason": "session_busy"})

        async with cursor(agent) as session:
            await session.send("long task")
            await session.adapter.on_interrupt(DEFAULT_ROOM, ControlMode.INTERRUPT)
            unwinding = TranscriptTools()
            with pytest.raises(InputNotAccepted):
                await session.send("next", tools=unwinding)
            cancelled.set()
            await accepted_work_finished(session.room())
            busy = TranscriptTools()
            with pytest.raises(InputNotAccepted):
                await session.send("next", tools=busy)

        assert unwinding.reply.errors == [] and busy.reply.errors == []
        assert len(agent.prompts) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "lost"),
        [
            # The agent's error is reported, then judged once that post is lost.
            (RequestError.internal_error(), 1),
            (RequestError(-32003, "busy", {"reason": "session_busy"}), 0),
        ],
        ids=["agent-error", "session-busy"],
    )
    async def test_a_slow_failure_report_keeps_the_room_until_it_lands(
        self, failure: RequestError, lost: int
    ) -> None:
        agent = FakeACPAgent()
        answers: list[dict[str, object]] = []

        @agent.on_prompt
        async def fail_then_ask(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                raise failure
            answers.append(
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
            )

        async with cursor(agent) as session:
            failing = SlowFailureReports(lost=lost)
            asked = await session.send("ask", tools=failing)
            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await failing.reporting.received.wait()

            with pytest.raises(InputNotAccepted):
                await session.send("next")
            failing.reporting.release.set()
            await accepted_work_finished(session.room())
            retried = await session.send("next")
            await question_asked(retried)
            await session.send(f"/cursor answer {question_token(retried)} mode=agent")
            await accepted_work_finished(session.room())

        assert answers == [answered("agent")]

    @pytest.mark.asyncio
    async def test_a_report_lost_in_the_lease_is_not_retried_after_it(
        self,
    ) -> None:
        agent = FakeACPAgent()
        answers: list[dict[str, object]] = []

        @agent.on_prompt
        async def busy_then_ask(peer: FakeACPAgent, session_id: str) -> None:
            if len(peer.prompts) == 1:
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
                raise RequestError(-32003, "busy", {"reason": "session_busy"})
            answers.append(
                await peer.ask_cursor_question(session_id, "mode", "agent", "plan")
            )

        async with cursor(agent) as session:
            failing = SlowFailureReports(lost=1)
            asked = await session.send("ask", tools=failing)
            await session.send(f"/cursor answer {question_token(asked)} mode=plan")
            await accepted_work_finished(session.room())
            retried = await session.send("next")
            await question_asked(retried)
            await session.send(f"/cursor answer {question_token(retried)} mode=agent")
            await accepted_work_finished(session.room())

            assert not failing.reporting.received.is_set()
        assert answers == [answered("agent")]

    @requires_eager_tasks
    @pytest.mark.asyncio
    async def test_a_turn_run_by_an_eager_task_factory_is_owned_first(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with cursor(agent) as session:
            with eager_tasks():
                served = await session.send("hello")

        assert served.errors == [] and len(agent.prompts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "turn_error"),
    [(ExitStage.EOF, None), (ExitStage.PROMPT, ACPTurnTimeoutError)],
    ids=["exits-at-close", "exited-before-close"],
)
async def test_a_descendant_holding_the_agents_pipes_keeps_its_room_owned(
    tmp_path: Path,
    stage: ExitStage,
    turn_error: type[Exception] | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The agent exits, at close or before it, but a background descendant
    still holds its stdout, so closure is unconfirmed: the room keeps its
    process until the descendant is gone and a retry confirms the exit."""
    monkeypatch.setattr(client_runtime, "STDOUT_HELD_WARNING_SECONDS", 0.1)
    holder_pid_file = tmp_path / "holder.pid"
    adapter = ACPClientAdapter(
        stdio_peer_config(
            stage,
            [],
            orphan_pid_file=holder_pid_file,
            retention=RoomRetentionConfig(transition_timeout_s=2.0),
            # A held stdout hides the agent's death, so its prompt only ends
            # at the turn deadline, whose recovery then waits on the pipes.
            turn_timeout_s=1.0,
        ),
        workspace_for_room=lambda room_id: str(REPO_ROOT),
    )
    await adapter.on_started("Stdio peer", "Real subprocess")
    try:
        with nullcontext() if turn_error is None else pytest.raises(turn_error):
            await send_to(adapter, DEFAULT_ROOM)
        room = adapter._rooms[DEFAULT_ROOM]
        runtime = room.runtime
        assert runtime is not None

        with pytest.raises(CleanupIncompleteError):
            await adapter.on_cleanup(DEFAULT_ROOM)

        assert room.runtime is runtime and not runtime.closed
        assert room.retained.snapshot.state == Residency.SUSPENDING
        process = runtime._transport
        assert isinstance(process, asyncio.subprocess.Process)
        held = f"ACP agent subprocess {process.pid} (exit code"
        assert any(warning.startswith(held) for warning in runtime_warnings(caplog))

        _stop(int(holder_pid_file.read_text()))
        await adapter.on_cleanup(DEFAULT_ROOM)

        assert runtime.closed
        assert DEFAULT_ROOM not in adapter._rooms
    finally:
        if holder_pid_file.exists():
            _stop(int(holder_pid_file.read_text()))
        await adapter.cleanup_all()


@pytest.mark.asyncio
async def test_a_retry_after_a_failed_exit_kills_the_running_agent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The agent's exit fails and leaves it running; a retry kills that same
    process and confirms its closure before the room is released."""
    adapter = ACPClientAdapter(
        stdio_peer_config(ExitStage.EOF, []),
        workspace_for_room=lambda room_id: str(REPO_ROOT),
    )
    async with FailingExits() as spawns:
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawns)
        await adapter.on_started("Stdio peer", "Real subprocess")
        try:
            await send_to(adapter, DEFAULT_ROOM)
            room = adapter._rooms[DEFAULT_ROOM]
            runtime = room.runtime
            [process] = spawns.processes

            with pytest.raises(RuntimeError, match=EXIT_FAILURE):
                await adapter.on_cleanup(DEFAULT_ROOM)

            assert process.returncode is None
            assert room.runtime is runtime

            await adapter.on_cleanup(DEFAULT_ROOM)

            assert process.returncode is not None
            assert DEFAULT_ROOM not in adapter._rooms
            assert runtime_warnings(caplog) == [
                f"ACP agent subprocess {process.pid} failed to exit",
                f"Killing ACP agent subprocess {process.pid} after its exit failed",
            ]
        finally:
            await adapter.cleanup_all()


def runtime_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == client_runtime.__name__ and record.levelno >= logging.WARNING
    ]


def _stop(pid: int) -> None:
    with suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGTERM)
