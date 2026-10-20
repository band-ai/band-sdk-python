from __future__ import annotations

import asyncio
from unittest.mock import patch

from band.adapters.opencode import OpencodeAdapterConfig
from band.adapters.opencode.approvals import ApprovalPorts, RoomApprovals
from band.runtime.working_state import end_tool_activity, report_tool_activity
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.opencode.helpers import AskFactory, FakeOpencodeClient


class TestOpenCodePermissionActivity:
    async def test_last_wait_includes_claimed_reply_transport_and_latest_progress(
        self,
    ) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        client = FakeOpencodeClient()
        entered, release = asyncio.Event(), asyncio.Event()

        async def abort() -> None:
            await client.abort_session("session")

        approvals = RoomApprovals(
            OpencodeAdapterConfig(),
            ApprovalPorts(
                room_id="room",
                client=lambda: client,
                tools=lambda: tools,
                turn_mentions=list,
                release_turn_wait=lambda: None,
                fail_turn=lambda message: None,
                abort_session=abort,
                is_own_band_tool=lambda permission: False,
            ),
        )
        asks = AskFactory()
        await approvals.on_permission_asked(asks.permission("first"))
        await approvals.on_permission_asked(asks.permission("second"))
        original = client.reply_permission

        async def held_reply(
            permission_id: str, *, reply: str, message: str | None = None
        ) -> None:
            if permission_id == "first":
                entered.set()
                await release.wait()
            await original(permission_id, reply=reply, message=message)

        with patch.object(client, "reply_permission", held_reply):
            first = asyncio.create_task(
                approvals.try_handle_reply("approve first", "user")
            )
            try:
                await entered.wait()
                assert await approvals.try_handle_reply("approve second", "user")
                await report_tool_activity(tools, "ordinary-tool")
                await end_tool_activity(tools)
                assert approvals.awaiting_human()
                assert recorder.waiting
            finally:
                release.set()
                await first
                approvals.cancel()
        assert not approvals.awaiting_human()
        assert recorder.events[-1] == {"waiting": False}
