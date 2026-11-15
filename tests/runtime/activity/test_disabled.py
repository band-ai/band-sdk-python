from __future__ import annotations

from unittest.mock import AsyncMock

from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.activity.kit import ActivityPeer, activity_context
from tests.runtime.scheduler.driver import attempt_delivery


class TestActivityDisabled:
    async def test_disabled_working_state_keeps_its_existing_contract(
        self,
    ) -> None:
        peer = ActivityPeer()
        handler = BlockingHandler(block=False)
        async with activity_context(peer, handler, AsyncMock(), enabled=False) as ctx:
            await attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            assert handler.completed == ["trigger"]
            assert peer.activity_reports == []
