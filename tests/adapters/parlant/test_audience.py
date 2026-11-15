from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.parlant.responses import relay_agent_response
from band.testing import FakeAgentTools
from tests.adapters.audiences import AGENT
from tests.adapters.parlant.helpers import SESSION_ID, agent_event

pytestmark = pytest.mark.usefixtures("parlant_sessions")


class TestParlantAudienceCompletion:
    @pytest.mark.parametrize(
        "text", ["Finished.", ""], ids=["visible-final", "empty-final"]
    )
    async def test_a_final_event_settles_after_all_originators_left(
        self, mock_app, text: str
    ) -> None:
        tools = FakeAgentTools(participants=[AGENT])
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
        mock_app.sessions.find_events = AsyncMock(
            return_value=[agent_event(text, offset=1)]
        )
        abandoned = await relay_agent_response(
            app=mock_app,
            session_id=SESSION_ID,
            room_id=tools.room_id,
            min_offset=0,
            tools=tools,
            audience=["departed"],
            timeout=5,
            poll=1,
        )
        assert not abandoned
        assert tools.turn.complete
        assert not tools.turn.replied
        assert tools.messages_sent == []

    async def test_poll_failure_does_not_settle_an_exhausted_audience(
        self, mock_app
    ) -> None:
        tools = FakeAgentTools(participants=[AGENT])
        mock_app.sessions.wait_for_more_events = AsyncMock(
            side_effect=RuntimeError("provider failed")
        )
        await relay_agent_response(
            app=mock_app,
            session_id=SESSION_ID,
            room_id=tools.room_id,
            min_offset=0,
            tools=tools,
            audience=["departed"],
            timeout=5,
            poll=1,
        )
        assert not tools.turn.complete
        assert tools.messages_sent == []
