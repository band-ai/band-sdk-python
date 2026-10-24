"""A batch turn and a singleton turn share Cursor's one turn per room."""

from __future__ import annotations

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    ROOM_B,
    BusyKind,
    busy_provider,
    deliver,
    is_accepted,
    occupy,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent

HOLDING_THEN_ARRIVING = [
    pytest.param(False, True, id="singleton-holds-batch-arrives"),
    pytest.param(True, False, id="batch-holds-singleton-arrives"),
    pytest.param(True, True, id="batch-holds-batch-arrives"),
]


class TestCursorBatchAdmission:
    async def test_a_batch_runs_as_one_provider_prompt(
        self, open_cursor: OpenCursor
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        session = await open_cursor(agent)

        await deliver(session, "go", room=ROOM_A, batch=True)

        assert len(agent.prompts) == 1

    @pytest.mark.parametrize("kind", ["prompt", "approval"])
    @pytest.mark.parametrize(("holds_batch", "arrives_as_batch"), HOLDING_THEN_ARRIVING)
    async def test_the_rooms_turn_is_shared_whichever_entry_holds_it_or_arrives(
        self,
        open_cursor: OpenCursor,
        kind: BusyKind,
        holds_batch: bool,
        arrives_as_batch: bool,
    ) -> None:
        provider = busy_provider(kind)
        session = await open_cursor(provider.agent)
        await occupy(session, provider, batch=holds_batch)

        with pytest.raises(InputNotAccepted):
            await deliver(session, "hello", room=ROOM_A, batch=arrives_as_batch)

        assert len(provider.agent.prompts) == 1
        assert session.last_reply.texts == []

    @pytest.mark.parametrize("kind", ["prompt", "approval"])
    @pytest.mark.parametrize(("holds_batch", "arrives_as_batch"), HOLDING_THEN_ARRIVING)
    async def test_another_room_is_served_whichever_entry_holds_the_first(
        self,
        open_cursor: OpenCursor,
        kind: BusyKind,
        holds_batch: bool,
        arrives_as_batch: bool,
    ) -> None:
        provider = busy_provider(kind, on_prompts=(1,))
        session = await open_cursor(provider.agent)
        await occupy(session, provider, room=ROOM_A, batch=holds_batch)

        assert await is_accepted(session, room=ROOM_B, batch=arrives_as_batch)
        assert len(provider.agent.prompts) == 2
