"""A wake restores the retained session exactly or fails keeping it."""

from __future__ import annotations

import pytest

from band.core.model_catalog import ModelSelection
from band.integrations.acp import sessions
from band.integrations.acp.session_config import ACPConfigError
from band.integrations.acp.sessions import ACPSessionRestoreError
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.agent import MODEL_OPTION_ID
from tests.integrations.acp.roomownership.kit import (
    model_select,
    models,
    recover,
    woken,
)
from tests.mcpclient import crash_backend


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
