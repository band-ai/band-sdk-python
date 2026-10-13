"""A Cursor message that arrives while the room's turn is parked stays
actionable: the host keeps it unacknowledged instead of the adapter queueing
it behind the turn."""

from __future__ import annotations

import httpx
import pytest

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.client.streaming import ControlMode
from band.platform.link import BandLink
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent, allow_or_reject
from tests.integrations.acp.acp_toolkit.harness import pair_in_process
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
    run_through,
)

ALICE = {"id": "user-456", "name": "Alice", "handle": "alice", "type": "User"}


class PostingPlatform(LifecyclePlatform):
    """A running room whose participants are listed and whose posts land."""

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return httpx.Response(
                200,
                json={"data": [ALICE], "metadata": {"has_more": False, "limit": 50}},
            )
        if request.method == "POST" and tail == "events":
            return httpx.Response(
                201, json={"data": {"id": "e-1", "message_type": "event"}}
            )
        if request.method == "POST" and tail == "messages":
            return httpx.Response(201, json={"data": {"id": "m-1", "recipients": []}})
        return super().answer(request)


class TestCursorMessageDelivery:
    @pytest.mark.asyncio
    async def test_a_message_behind_a_parked_turn_is_never_acknowledged_unrun(
        self,
    ) -> None:
        agent = FakeACPAgent().will_ask_permission(
            title="shell", options=allow_or_reject("allow-once")
        )
        adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))
        pair_in_process(adapter, agent)
        await adapter.on_started("Cursor", "under test")
        peer = PostingPlatform()
        try:
            async with rest_client_over(peer.answer) as rest:
                link = BandLink(agent_id=AGENT_ID, api_key="test-key")
                link.rest = rest
                ctx = run_through(
                    adapter,
                    link,
                    config=SessionConfig(
                        enable_context_hydration=False, enable_working_state=False
                    ),
                )
                ctx.add_participant(ALICE)
                await ctx._process_event(
                    make_message_event(room_id=ROOM_ID, msg_id="A", content="run it")
                )
                await ctx._process_event(
                    make_message_event(room_id=ROOM_ID, msg_id="B", content="then this")
                )
                await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
                await ctx.stop()
        finally:
            await adapter.stop()

        assert len(agent.prompts) == 1
        assert ("A", "processed") in peer.accepted_marks
        assert ("B", "processed") not in peer.accepted_marks
