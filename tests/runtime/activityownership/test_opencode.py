from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import patch

import pytest

from band.adapters.opencode import OpencodeAdapter
from band.client.streaming import ControlMode
from band.integrations.opencode.types import OpencodeSessionState
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_permission,
    make_platform_message,
)
from tests.conftest import make_message_event
from tests.mcpbackends import backends_created_by
from tests.runtime.helpers import ROOM_ID, LifecyclePlatform


class TestOpenCodeLifetime:
    @pytest.mark.parametrize("mode", [ControlMode.STOP, ControlMode.INTERRUPT])
    async def test_parked_native_turn_dual_dispatch_aborts_remote_once(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
        mode: ControlMode,
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "permission-1")]]
        )
        adapter = OpencodeAdapter(client_factory=lambda _: client, emit=())
        aborting, release, cleaned = (asyncio.Event() for _ in range(3))
        original_abort = client.abort_session

        async def abort(session_id: str) -> None:
            aborting.set()
            await release.wait()
            await original_abort(session_id)
            cleaned.set()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            await adapter.on_message(
                make_platform_message(room_id=ROOM_ID, sender_id="user-456"),
                AgentTools.from_context(ctx),
                OpencodeSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id=ROOM_ID,
            )

        with backends_created_by(), patch.object(client, "abort_session", abort):
            await adapter.on_started("Test", "Test")
            ctx = execution(handler)
            try:
                await ctx._process_event(make_message_event(msg_id="original"))
                assert activity_platform.working_transitions == [True]
                assert activity_platform.marked("processed") == ["original"]
                assert ctx.interrupt(kind=mode)
                notify = asyncio.create_task(adapter.on_interrupt(ROOM_ID, mode))
                await aborting.wait()
                assert not cleaned.is_set()
                release.set()
                await notify
                await adapter.backend_turns.settled(ROOM_ID)
                await ctx._join_activity_completion()
                assert client.aborted_sessions == ["sess-1"]
                assert cleaned.is_set()
                assert activity_platform.working_transitions == [True, False]
                assert activity_platform.marked("processed") == ["original"]
            finally:
                release.set()
                await adapter.on_cleanup(ROOM_ID)
