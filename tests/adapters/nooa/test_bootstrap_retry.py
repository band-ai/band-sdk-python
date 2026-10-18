"""Setup failures must leave history available to the first room agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from band.adapters.nooa import NooaAdapter, NooaRoomAgent
from band.adapters.nooa.adapter import TurnObserver
from band.adapters.nooa.tools import RoomTools
from band.client.rest import AsyncRestClient
from band.core.protocols import AgentToolsProtocol
from band.runtime.tools import BandTool
from band.testing.nooa import room_agent_class, room_call, scripted_llm
from tests.adapters.nooa.conftest import BOOTSTRAP_NOTE, ROSTER_PERSON
from tests.adapters.nooa.kit import (
    ROOM_ID,
    coordinated_context,
    failing_llm,
    prompt_text,
)
from tests.conftest import make_message_event


class TestBootstrapRetry:
    @pytest.mark.parametrize(
        "failure_phase", ["none", "constructor", "observer", "binding"]
    )
    @pytest.mark.parametrize(
        "later_message", [False, True], ids=["same-message", "later-message"]
    )
    async def test_history_reaches_first_successful_room_agent(
        self,
        history_rest: AsyncRestClient,
        failure_phase: str,
        later_message: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        attempts = 0

        def initialize(agent: NooaRoomAgent) -> None:
            nonlocal attempts
            attempts += 1
            if failure_phase == "constructor" and attempts == 1:
                raise RuntimeError("Agent dependency temporarily unavailable")
            NooaRoomAgent.__init__(agent)

        setup_failed = False
        observe = TurnObserver.observe
        bind = RoomTools.bound_to

        def observe_once(observer: TurnObserver, room_agent: NooaRoomAgent) -> None:
            nonlocal setup_failed
            if failure_phase == "observer" and not setup_failed:
                setup_failed = True
                raise RuntimeError("Agent dependency temporarily unavailable")
            observe(observer, room_agent)

        @asynccontextmanager
        async def bind_once(
            room: RoomTools, tools: AgentToolsProtocol
        ) -> AsyncIterator[None]:
            nonlocal setup_failed
            if failure_phase == "binding" and not setup_failed:
                setup_failed = True
                raise RuntimeError("Agent dependency temporarily unavailable")
            async with bind(room, tools):
                yield

        monkeypatch.setattr(TurnObserver, "observe", observe_once)
        monkeypatch.setattr(RoomTools, "bound_to", bind_once)
        llm = scripted_llm(room_call(BandTool.NO_REPLY))
        adapter = NooaAdapter(
            agent=room_agent_class(llm, namespace={"__init__": initialize}), emit=()
        )
        await adapter.on_started("Agent", "A test agent")
        ctx = coordinated_context(adapter, history_rest)
        ctx.set_participants([ROSTER_PERSON])
        ctx.inject_system_message("CONTACT-DELTA-ONE")
        event = make_message_event(room_id=ROOM_ID)

        try:
            if failure_phase != "none":
                with pytest.raises(
                    RuntimeError, match="dependency temporarily unavailable"
                ):
                    await ctx._on_execute(ctx, event)
            if later_message:
                event = make_message_event(
                    room_id=ROOM_ID,
                    msg_id="later-message",
                    content="What is the deployment window called?",
                )
            await ctx._on_execute(ctx, event)

            assert BOOTSTRAP_NOTE in prompt_text(llm)
            assert "CONTACT-DELTA-ONE" in prompt_text(llm)
            assert ROSTER_PERSON["name"] in prompt_text(llm)
            assert ctx.peek_pending_system_messages() == []
            assert ctx.is_llm_initialized
        finally:
            await adapter.cleanup_all()

    async def test_provider_failure_consumes_prepared_context(
        self, history_rest: AsyncRestClient
    ) -> None:
        llm = failing_llm("provider unavailable")
        adapter = NooaAdapter(agent=room_agent_class(llm), emit=())
        await adapter.on_started("Agent", "Test agent")
        ctx = coordinated_context(adapter, history_rest)
        ctx.set_participants([ROSTER_PERSON])
        ctx.inject_system_message("CONTACT-DELTA-ONE")
        try:
            with pytest.raises(Exception, match="provider unavailable"):
                await ctx._on_execute(ctx, make_message_event(room_id=ROOM_ID))
            assert ctx.is_llm_initialized
            assert not ctx.participants_changed()
            assert ctx.peek_pending_system_messages() == []
        finally:
            await adapter.cleanup_all()
