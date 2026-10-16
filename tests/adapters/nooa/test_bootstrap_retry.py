"""A room whose agent could not be constructed still needs its bootstrap history."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.nooa import NooaAdapter, NooaRoomAgent
from band.agent import Agent
from band.runtime.execution import ExecutionContext
from band.testing.nooa import room_agent_class, room_call, scripted_llm
from tests.adapters.nooa.kit import ROOM_ID, prompt_text
from tests.conftest import make_message_event


class TestBootstrapRetry:
    @pytest.mark.parametrize(
        "fail_first", [False, True], ids=["normal-bootstrap", "constructor-retry"]
    )
    async def test_history_reaches_first_successful_room_agent(
        self, mock_rest_client: MagicMock, fail_first: bool
    ) -> None:
        attempts = 0

        def initialize(agent: NooaRoomAgent) -> None:
            nonlocal attempts
            attempts += 1
            if fail_first and attempts == 1:
                raise RuntimeError("Agent dependency temporarily unavailable")
            NooaRoomAgent.__init__(agent)

        llm = scripted_llm(room_call("band_no_reply"))
        adapter = NooaAdapter(
            agent=room_agent_class(llm, namespace={"__init__": initialize}),
            emit=(),
        )
        await adapter.on_started("Agent", "A test agent")
        agent = Agent(runtime=MagicMock(agent_id="agent-123"), adapter=adapter)
        ctx = ExecutionContext(ROOM_ID, MagicMock(rest=mock_rest_client), AsyncMock())
        ctx.set_participants([{"id": "user-456", "name": "Alice", "type": "User"}])
        event = make_message_event(room_id=ROOM_ID)
        note = "The deployment window is named BLUE-PINE."
        ctx.get_context = AsyncMock(
            return_value=MagicMock(
                messages=[
                    {
                        "id": "earlier-message",
                        "sender_name": "Alice",
                        "sender_type": "User",
                        "content": note,
                    },
                    {"id": event.payload.id, "content": event.payload.content},
                ]
            )
        )

        if fail_first:
            with pytest.raises(
                RuntimeError, match="dependency temporarily unavailable"
            ):
                await agent._on_execute(ctx, event)
        await agent._on_execute(ctx, event)

        assert note in prompt_text(llm)
