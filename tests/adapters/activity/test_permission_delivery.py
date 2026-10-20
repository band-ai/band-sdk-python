from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from band.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexCommand
from band.integrations.codex import CodexApprovalMethod
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.codex.kit import make_platform_message, wire_codex_room
from tests.adapters.codexturns import event_request
from tests.adapters.opencode.helpers import wait_for


class TestPermissionDelivery:
    async def test_codex_retains_claimed_permission_until_provider_response_finishes(
        self,
    ) -> None:
        adapter = CodexAdapter(CodexAdapterConfig(approval_mode="manual"), emit=())
        await adapter.on_started("Test", "Test")
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request_id: int, result: dict[str, Any]) -> None:
            entered.set()
            await release.wait()

        client = AsyncMock()
        client.respond.side_effect = respond
        wire_codex_room(adapter, client)
        params = {"command": "private-command", "approvalId": "approval"}
        pending = asyncio.create_task(
            adapter._handle_approval_request(
                client=client,
                tools=tools,
                msg=make_platform_message(),
                room_id="room-1",
                event=event_request(1, CodexApprovalMethod.COMMAND_EXECUTION, params),
                params=params,
            )
        )
        try:
            await wait_for(lambda: bool(tools.messages_sent))
            await adapter._handle_approval_command(
                tools=FakeAgentTools(),
                msg=make_platform_message(),
                room_id="room-1",
                command=CodexCommand.APPROVE,
                args="",
            )
            await entered.wait()
            assert recorder.waiting
            assert adapter._pending_approvals["room-1"].has_claimed()
        finally:
            release.set()
            await pending
        assert not recorder.waiting
        assert not adapter._pending_approvals["room-1"]
