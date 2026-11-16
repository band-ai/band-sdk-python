"""A failed turn retires its process through the owner and keeps the session."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from acp import RequestError
from acp.schema import LoadSessionResponse

from band.core.model_catalog import ModelSelection
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.core.retention import Residency
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.sessions import (
    ACP_SESSION_LOAD_TIMEOUT_SECONDS,
    ACPSessionRestoreError,
)
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.core.retention.kit import ManualClock
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
    released,
)
from tests.integrations.acp.roomownership.kit import (
    fails_prompt,
    model_select,
    models,
    recover,
)
from tests.toolkit.tasks import background

PERSISTED_SESSION = "persisted"


def persisted_history() -> ACPClientSessionState:
    """The bootstrap history that names the room's persisted session."""
    return ACPClientSessionState(room_to_session={DEFAULT_ROOM: PERSISTED_SESSION})


class TestRecovery:
    """A failed turn retires its process through the owner and keeps the session."""

    @pytest.mark.asyncio
    async def test_the_next_turn_reloads_the_recovered_session(self) -> None:
        agent = FakeACPAgent(supports_session_load=True)
        fails_prompt(
            agent, 2, RequestError.internal_error({"detail": "process wedged"})
        )

        async with acp_adapter(agent) as session:
            await session.send("seeding turn")
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
    async def test_an_unanswered_session_load_creates_no_session_on_that_process(
        self, loop_clock: ManualClock
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True).will_say("ok")
        agent.knows_session(PERSISTED_SESSION)
        loading = agent.holds_next_session_load()
        async with (
            acp_adapter(agent) as session,
            background(
                session.send("hello", bootstrap=True, history=persisted_history())
            ) as first,
        ):
            await loading.received.wait()
            unanswered = session.runtime()
            await loop_clock.advance(ACP_SESSION_LOAD_TIMEOUT_SECONDS)
            with pytest.raises(ACPSessionRestoreError, match="no answer"):
                await first
            failed = session.last_reply
            assert agent.sessions == []
            assert agent.prompts == []

            loading.release.set()
            served = await session.send("again")

            assert failed.errors == [GENERIC_PROVIDER_FAILURE_MESSAGE]
            assert unanswered.closed
            assert session.runtime() is not unanswered
            assert served.errors == []
        assert len(agent.sessions) == 1

    @pytest.mark.asyncio
    async def test_an_abandoned_session_load_retires_its_process_before_a_new_session(
        self,
    ) -> None:
        agent = FakeACPAgent(supports_session_load=True).will_say("ok")
        agent.knows_session(PERSISTED_SESSION)
        loading = agent.holds_next_session_load()
        async with (
            acp_adapter(agent) as session,
            background(
                session.send("hello", bootstrap=True, history=persisted_history())
            ) as first,
        ):
            await loading.received.wait()
            unanswered = session.runtime()
            room = session.room()
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await released(room)

            served = await session.send("again")

            assert unanswered.closed
            assert session.runtime() is not unanswered
            assert served.errors == []
        assert agent.session_load_requests == [PERSISTED_SESSION]
        assert len(agent.sessions) == 1

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
            async def load_session(
                self,
                cwd: str,
                session_id: str,
                mcp_servers: Any = None,
                **kwargs: Any,
            ) -> LoadSessionResponse:
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
        fails_prompt(agent, 2, RequestError.internal_error())

        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("seeding turn")
            with pytest.raises(RequestError):
                await session.send("hello")
            failed = session.last_reply
            agent.knows_session(session.session_id(DEFAULT_ROOM))

            restoring = await session.send("again")

        assert restoring.texts == [reply]
        assert reply not in failed.texts
