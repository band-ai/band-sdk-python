from __future__ import annotations

from band.adapters.agno import (
    AgnoAdapter,
)
from band.testing import FakeAgentTools
from tests.adapters.agno.adapter.kit import _msg


class TestSessionIsolation:
    async def test_arun_uses_room_id_as_session_id(self, make_started_adapter):
        adapter, agent = await make_started_adapter()

        await adapter.on_message(
            _msg("room-A", "hi"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )

        assert agent.arun.call_args.kwargs["session_id"] == "room-A"

    async def test_custom_session_id_factory_is_used(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(
            agent=agent, session_id_factory=lambda room: f"sess::{room}", emit=()
        )
        await adapter.on_started("TestBot", "desc")

        await adapter.on_message(
            _msg("room-A", "hi"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )

        assert agent.arun.call_args.kwargs["session_id"] == "sess::room-A"

    async def test_two_rooms_get_isolated_sessions_and_inputs(
        self, make_started_adapter
    ):
        adapter, agent = await make_started_adapter()

        await adapter.on_message(
            _msg("room-A", "alpha-secret"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )
        await adapter.on_message(
            _msg("room-B", "beta-secret"),
            FakeAgentTools(),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-B",
        )

        calls = agent.arun.call_args_list
        assert calls[0].kwargs["session_id"] == "room-A"
        assert calls[1].kwargs["session_id"] == "room-B"

        room_b_input = " ".join(m.content or "" for m in calls[1].kwargs["input"])
        assert "beta-secret" in room_b_input
        assert "alpha-secret" not in room_b_input
