"""STOP, INTERRUPT and room retirement cancel Cursor's provider task."""

from __future__ import annotations

import asyncio

import pytest

from band.core.turn_lifecycle import TURN_SETTLE_GRACE_S
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    ROOM_B,
    BusyKind,
    TurnEnd,
    active_turn,
    busy_provider,
    cancels_sent,
    cursor_adapter,
    interrupt,
    occupy,
    provider_task,
    retire,
    stop,
    terminated,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class TestCursorProviderCancellation:
    @pytest.mark.parametrize(
        ("ending", "kind"),
        [
            pytest.param(ending, kind, id=f"{kind}-{ending.__name__}")
            for ending in (stop, interrupt, retire)
            for kind in ("prompt", "approval")
            if (ending, kind) != (retire, "approval")
        ],
    )
    async def test_the_peer_is_told_to_cancel_the_active_prompt(
        self, open_cursor: OpenCursor, kind: BusyKind, ending: TurnEnd
    ) -> None:
        provider = busy_provider(kind)
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider)
        turn = active_turn(session)

        await ending(cursor_adapter(session), ROOM_A)
        await terminated(session, turn)
        await asyncio.gather(sending, return_exceptions=True)

        assert cancels_sent(provider.agent) == [provider.agent.session_ids()[0]]

    async def test_retiring_a_room_parked_on_a_decision_declines_it_instead_of_cancelling(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(
            provider.agent, turn_settle_timeout_s=TURN_SETTLE_GRACE_S
        )
        sending = await occupy(session, provider)
        turn = active_turn(session)

        await retire(cursor_adapter(session), ROOM_A)
        await terminated(session, turn)
        await asyncio.gather(sending, return_exceptions=True)

        assert provider.agent.approved is False
        assert cancels_sent(provider.agent) == []

    async def test_a_repeated_stop_sends_one_cancel(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)
        turn = active_turn(session)

        try:
            await stop(cursor_adapter(session), ROOM_A)
            await stop(cursor_adapter(session), ROOM_A)
            await terminated(session, turn)
        finally:
            provider.finish.set()
            await terminated(session, turn)

        assert cancels_sent(provider.agent) == [provider.agent.session_ids()[0]]

    async def test_stopping_another_room_leaves_the_active_prompt_running(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)

        await stop(cursor_adapter(session), ROOM_B)

        assert not provider_task(session).cancelling()
        assert cancels_sent(provider.agent) == []

    async def test_cancelling_before_the_task_starts_never_reaches_the_peer(
        self, open_cursor: OpenCursor
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        session = await open_cursor(agent)

        # Queued after the delivery's first step and ahead of the provider
        # task's, so the task is cancelled before its first instruction.
        sending = asyncio.create_task(session.send("hello", room=ROOM_A))
        asyncio.get_running_loop().call_soon(lambda: provider_task(session).cancel())
        await asyncio.gather(sending, return_exceptions=True)
        await session.send("hello again", room=ROOM_A)

        assert agent.session_ids() == ["fake-session-1"]
        assert len(agent.prompts) == 1
        assert cancels_sent(agent) == []
