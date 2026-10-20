from __future__ import annotations

from agno.models.message import Message

from band.adapters.agno import (
    AgnoAdapter,
)


class TestOnCleanup:
    async def test_drops_room_transcript(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)
        adapter._message_history["room-1"] = [Message(role="user", content="hi")]

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._message_history

    async def test_unknown_room_is_noop(self, make_agno_agent):
        agent = make_agno_agent()
        adapter = AgnoAdapter(agent=agent)

        await adapter.on_cleanup("never-seen")  # must not raise
