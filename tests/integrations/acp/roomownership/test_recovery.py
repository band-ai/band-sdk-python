"""A failed turn retires its process through the owner and keeps the session."""

from __future__ import annotations

import pytest
from acp import RequestError

from band.core.model_catalog import ModelSelection
from band.core.retention import ReleaseCause, ReleaseOutcome, Residency
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.roomownership.kit import model_select, models, recover


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
