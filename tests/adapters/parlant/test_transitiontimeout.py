"""Parlant setup, post, poll and drain waits end at ``transition_timeout_s``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.adapters.parlant.adapter import PROVIDER
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.parlant.engine import EngineSession, Holdout
from tests.adapters.parlant.helpers import (
    BAND_DESCRIPTION,
    BAND_NAME,
    SESSION_ID,
    RecordingBackgroundTasks,
    agent_event,
)

pytestmark = [pytest.mark.usefixtures("parlant_sessions"), pytest.mark.looptime]

TRANSITION_SECONDS = 3.0
POLL_SECONDS = 0.01
ROOM_ID = "room-123"

StartAdapter = Callable[..., Awaitable[ParlantAdapter]]
RunTurn = Callable[..., Awaitable[None]]


def bounded_config() -> ParlantAdapterConfig:
    return ParlantAdapterConfig(
        response_timeout=1.0,
        response_poll=POLL_SECONDS,
        transition_timeout_s=TRANSITION_SECONDS,
    )


class TestParlantTransitionTimeout:
    async def test_a_hung_setup_fails_the_start_and_leaves_the_borrowed_server(
        self, mock_parlant_server: MagicMock, mock_parlant_agent: MagicMock
    ) -> None:
        async def hang(server: object, agent: object) -> None:
            await asyncio.Future[None]()

        adapter = ParlantAdapter(
            bounded_config(),
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            configure=hang,
        )
        loop = asyncio.get_running_loop()
        began = loop.time()

        with pytest.raises(TimeoutError):
            await adapter.on_started(BAND_NAME, BAND_DESCRIPTION)

        assert loop.time() - began == TRANSITION_SECONDS
        assert mock_parlant_server.method_calls == []

    async def test_a_hung_message_post_fails_the_turn_at_the_bound(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        mock_tools: FakeAgentTools,
    ) -> None:
        async def hang(**_: object) -> None:
            await asyncio.Future[None]()

        mock_app.sessions.create_customer_message = AsyncMock(side_effect=hang)
        adapter = await start_adapter(bounded_config())
        loop = asyncio.get_running_loop()
        began = loop.time()

        with pytest.raises(TimeoutError):
            await run_turn(adapter)

        assert loop.time() - began == TRANSITION_SECONDS
        [failure] = reported_failures(mock_tools)
        assert (failure["provider"], failure["message"]) == (
            PROVIDER,
            GENERIC_PROVIDER_FAILURE_MESSAGE,
        )

    async def test_a_hung_poll_is_abandoned_at_the_bound(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        mock_tools: FakeAgentTools,
    ) -> None:
        async def hang(**_: object) -> bool:
            await asyncio.Future[None]()
            return True

        mock_app.sessions.wait_for_more_events = AsyncMock(side_effect=hang)
        adapter = await start_adapter(bounded_config())
        loop = asyncio.get_running_loop()
        began = loop.time()

        await run_turn(adapter)

        assert loop.time() - began == pytest.approx(TRANSITION_SECONDS + POLL_SECONDS)
        # A failed poll abandons waiting only: no reply, no sticky latch, no
        # failure report that would claim the provider is unavailable.
        assert mock_tools.messages_sent == []
        assert reported_failures(mock_tools) == []
        assert adapter.backend_turns.unconfirmed_handle(ROOM_ID) is None
        assert not adapter.backend_turns.unavailable(ROOM_ID)
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=False)
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        await run_turn(adapter)
        assert mock_app.sessions.create_customer_message.await_count == 1

    async def test_an_answered_turn_whose_engine_never_ends_is_stopped_at_the_bound(
        self,
        start_adapter: StartAdapter,
        run_turn: RunTurn,
        mock_app: MagicMock,
        mock_tools: FakeAgentTools,
        background_tasks: RecordingBackgroundTasks,
    ) -> None:
        engine = EngineSession(
            background_tasks, SESSION_ID, parent=Holdout(resists=False)
        )

        async def post(**_: object) -> MagicMock:
            await engine.start()
            return MagicMock(offset=1)

        mock_app.sessions.create_customer_message = AsyncMock(side_effect=post)
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
        mock_app.sessions.find_events = AsyncMock(
            return_value=[agent_event("Done.", offset=2)]
        )
        adapter = await start_adapter(bounded_config())
        loop = asyncio.get_running_loop()
        began = loop.time()

        with pytest.raises(TimeoutError):
            await run_turn(adapter)

        assert loop.time() - began == TRANSITION_SECONDS
        assert engine.parent.cancel_seen.is_set()
        assert adapter.backend_turns.unconfirmed_handle(ROOM_ID) is None
        [failure] = reported_failures(mock_tools)
        assert failure["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
