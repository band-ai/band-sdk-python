"""An ACP room owns its process, session and work until a release confirms it.

Each test drives a real adapter over real ACP JSON-RPC: an in-process fake
agent, or the stdio peer subprocess where the process boundary itself is under
test. Barriers hold the exact step a race needs; nothing sleeps for time.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import pytest
from acp import RequestError
from acp.schema import SessionConfigOptionSelect

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.core.model_catalog import ModelSelection
from band.core.retention import ReleaseCause, Residency, RoomRetentionConfig
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import CURSOR_ASK_QUESTION_METHOD
from band.integrations.acp.client_types import ACPClientSessionState, ACPRoom
from band.integrations.acp.model_selection import MODEL_CATEGORY
from band.integrations.acp.session_config import ACPConfigError
from band.integrations.acp.sessions import ACPSessionRestoreError
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    AcpSession,
    FakeACPAgent,
    Reply,
    accepted_work_finished,
    acp_adapter,
    decision_token,
    fake_agent_config,
    released,
    select_option,
    started_acp_adapter,
)
from tests.integrations.acp.acp_toolkit.agent import MODEL_OPTION_ID, Gate
from tests.integrations.acp.acp_toolkit.harness import TranscriptTools
from tests.integrations.acp.acp_toolkit.peer import ExitStage, stdio_peer_config
from tests.integrations.acp.conftest import make_platform_message
from tests.mcpbackends import backends_created_by
from tests.mcpclient import crash_backend
from tests.paths import REPO_ROOT

CANCELLED = {"outcome": {"outcome": "cancelled"}}


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

            async with woken(session.adapter, room):
                assert room.live_session is not None
                assert room.live_session[0] == retained
                live_url = agent.mcp_server(retained, BAND_MCP_SERVER_NAME).url

        assert agent.session_load_requests == [retained]
        assert agent.session_ids() == [retained]
        assert agent.config_selections(retained)[-1] == (MODEL_OPTION_ID, "b")
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
    async def test_a_failed_exit_keeps_the_process_and_a_retry_closes_it(
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
            with pytest.raises(RuntimeError, match="closing"):
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

            with pytest.raises(RuntimeError, match="closing"):
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
        config = fake_agent_config(
            inject_band_tools=True,
            retention=RoomRetentionConfig(transition_timeout_s=0.05),
        )
        with backends_created_by():
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()
                exiting = agent.exits_slowly()

                with pytest.raises(CleanupIncompleteError):
                    await session.adapter.cleanup_all()
                assert session.adapter._mcp.current is not None
                assert not runtime.closed

                exiting.release.set()
                await session.adapter.cleanup_all()

                assert runtime.closed
                assert session.adapter._mcp.current is None
                await session.adapter.on_started("Fake Agent", "restarted")

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
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(exiting.received.wait(), timeout=0.2)
            closing.release.set()
            await exiting.received.wait()

            assert agent.closed_sessions == [retired]
            exiting.release.set()
            await cleanup


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
async def cursor(agent: FakeACPAgent) -> AsyncIterator[AcpSession]:
    adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))
    async with started_acp_adapter(adapter, agent) as session:
        yield session


def answered(option: str) -> dict[str, object]:
    return {
        "outcome": {
            "outcome": "answered",
            "answers": [{"questionId": "mode", "selectedOptionIds": [option]}],
        }
    }


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

            async with asyncio.timeout(5):
                stale = await old_client.ext_method(
                    CURSOR_ASK_QUESTION_METHOD,
                    {"questions": [{"id": "mode", "options": [{"id": "agent"}]}]},
                )

            assert stale == CANCELLED
            assert len(session.adapter._pending_decisions) == 0
            parked.release.set()
            await successor
            assert session.last_reply.texts == []


@pytest.mark.asyncio
async def test_a_descendant_holding_the_agents_pipes_keeps_its_room_owned(
    tmp_path: Path,
) -> None:
    """The agent exits, but a background descendant still holds its stdio, so
    closure is unconfirmed: the room keeps its process and refuses a rejoin
    until the descendant is gone and a retry confirms the exit."""
    holder_pid_file = tmp_path / "holder.pid"
    adapter = ACPClientAdapter(
        stdio_peer_config(
            ExitStage.EOF,
            [],
            orphan_pid_file=holder_pid_file,
            retention=RoomRetentionConfig(transition_timeout_s=2.0),
        ),
        workspace_for_room=lambda room_id: str(REPO_ROOT),
    )
    message = make_platform_message("hello", room_id=DEFAULT_ROOM)
    await adapter.on_started("Stdio peer", "Real subprocess")
    try:
        await adapter.on_message(
            message,
            TranscriptTools(),
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id=DEFAULT_ROOM,
        )
        room = adapter._rooms[DEFAULT_ROOM]
        runtime = room.runtime
        assert runtime is not None

        with pytest.raises(CleanupIncompleteError):
            await adapter.on_cleanup(DEFAULT_ROOM)

        assert room.runtime is runtime and not runtime.closed
        assert room.retained.snapshot.state == Residency.SUSPENDING

        _stop(int(holder_pid_file.read_text()))
        await adapter.on_cleanup(DEFAULT_ROOM)

        assert runtime.closed
        assert DEFAULT_ROOM not in adapter._rooms
    finally:
        if holder_pid_file.exists():
            _stop(int(holder_pid_file.read_text()))
        await adapter.cleanup_all()


def _stop(pid: int) -> None:
    with suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGTERM)
