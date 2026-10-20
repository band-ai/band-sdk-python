from __future__ import annotations

from agno.agent import Agent as AgnoAgent

from band.adapters.agno import (
    AgnoAdapter,
)
from tests.adapters.agno.adapter.kit import _msg
from tests.adapters.agno.helpers import (
    CapturingModel,
    ContactAwareTools,
)


class TestPerRunToolExposureEndToEnd:
    """Drive a real Agno agent so we assert on the tools Agno actually offered
    the model per run -- proving the factory is installed and invoked per turn,
    and that contact tools do not leak across rooms through the shared agent."""

    async def test_model_receives_only_active_room_tools(self):
        model = CapturingModel()
        agno = AgnoAgent(model=model, instructions="You are Dev.")
        adapter = AgnoAdapter(agent=agno, emit=())
        await adapter.on_started("Bot", "desc")

        # Hub room: contact tools are offered to the model.
        hub = ContactAwareTools(hub_room_id="hub", room_id="hub")
        await adapter.on_message(
            _msg("hub", "hi"),
            hub,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="hub",
        )
        assert model.captured_tool_names is not None
        assert "band_add_contact" in model.captured_tool_names

        # Normal room afterwards on the same shared agent: no contact leak.
        normal = ContactAwareTools(room_id="room-A")
        await adapter.on_message(
            _msg("room-A", "hi"),
            normal,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-A",
        )
        assert model.captured_tool_names == ["band_send_message"]
