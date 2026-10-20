from __future__ import annotations

from collections.abc import Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.registry import MCP_TOOL_PREFIX
from tests.adapters.claude_sdk.fakecli import scripted_claude
from tests.adapters.opencode.helpers import make_platform_message
from tests.baseline.decisions import ModelDecision
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform
from tests.runtime.helpers import ROOM_ID


class TestClaudeNativeWireActivity:
    @pytest.mark.httpx_mock(should_mock=lambda request: request.url.host != "127.0.0.1")
    async def test_real_context_observes_native_tool_without_tool_narration(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        with scripted_claude() as cli:
            cli.script(
                [
                    ModelDecision.call("Bash", command="private-argument"),
                    ModelDecision.call(
                        f"{MCP_TOOL_PREFIX}band_send_message",
                        content="private-result",
                        mentions=["@alice"],
                    ),
                ]
            )
            adapter = ClaudeSDKAdapter(emit=())
            await adapter.on_started("Test", "Test")

            async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
                await adapter.on_message(
                    make_platform_message(
                        room_id=ROOM_ID, sender_id="user-456", sender_name="Alice"
                    ),
                    AgentTools.from_context(ctx),
                    ClaudeSDKSessionState(),
                    None,
                    None,
                    is_session_bootstrap=True,
                    room_id=ROOM_ID,
                )

            ctx = execution(handler)
            try:
                await ctx._process_event(make_message_event())
                assert {
                    "working": True,
                    "activity": {"kind": "tool_call", "name": "Bash"},
                } in activity_platform.activity_bodies
                assert activity_platform.activity_bodies[-1] == {"working": False}
                activity_platform.assert_no_tool_narration()
                activity_platform.assert_hides("private-argument", "private-result")
            finally:
                await adapter.cleanup_all()
