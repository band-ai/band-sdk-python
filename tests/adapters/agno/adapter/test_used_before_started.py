from __future__ import annotations

import pytest

from band.adapters.agno import (
    AgnoAdapter,
)
from band.testing import FakeAgentTools


class TestUsedBeforeStarted:
    async def test_run_agent_before_on_started_raises(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)

        with pytest.raises(RuntimeError, match="before on_started"):
            await adapter._run_agent(
                [], FakeAgentTools(), room_id="room-1", msg_ids="m1"
            )
