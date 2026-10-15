from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    StrandsAdapter,
    StrandsAdapterConfig,
)
from tests.adapters.strands.kit import ROOM, SEND_TURN, _run_message


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_unknown_room_is_noop(self):
        adapter = StrandsAdapter(StrandsAdapterConfig(model="m"))
        await adapter.on_cleanup("never-seen-room")  # must not raise

    @pytest.mark.asyncio
    async def test_cleanup_removes_room_history(self, tools, scripted):
        adapter = await scripted(SEND_TURN)
        await _run_message(adapter, tools)
        assert ROOM in adapter._message_history

        await adapter.on_cleanup(ROOM)

        assert ROOM not in adapter._message_history
