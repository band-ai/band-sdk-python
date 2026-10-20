from __future__ import annotations

import asyncio

import pytest

from tests.baseline.adapter import Adapter
from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.activity.kit import (
    HeldNativePeer,
    assert_no_tool_narration,
    native_activity_agent,
)
from tests.e2e.baseline.smoke.samples.approvals import CLAUDE_SHELL_TOOLS
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestNativeActivity:
    @per_adapter(Adapter.CLAUDE_SDK)
    @pytest.mark.asyncio(loop_scope="session")
    @pytest.mark.timeout(extra=120)
    async def test_native_shell_reports_privately_without_narration(
        self,
        cell: AdapterCell,
        native_peer: HeldNativePeer,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        async with native_activity_agent(cell) as agent:
            try:
                room_id = await cell.resources.provision_room(
                    title="activity-native", participants=[agent.id]
                )
                async with reply_capture(room_id, observe_activity=True) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        f"Execute this exact command once through native Bash/PowerShell, then send me its actual output: {native_peer.command}",
                        mention_id=agent.id,
                        mention_name=agent.name,
                    )
                    async with asyncio.timeout(cell.settings.e2e_timeout):
                        await native_peer.entered.wait()
                    await capture.wait_for_tool_activity(agent.id, CLAUDE_SHELL_TOOLS)
                    mark = capture.activities.snapshot()
                    native_peer.release.set()
                    replies = await capture.wait_for_reply(mid, agent.id)
                    await capture.wait_for_activity_stop(agent.id, since=mark)
                    assert (
                        f"GET /{native_peer.argument_sentinel} HTTP/1.1"
                        in native_peer.requests
                    )
                    replies.assert_contains_any([native_peer.result_sentinel])
                    capture.activities.assert_names_only(
                        native_peer.argument_sentinel, native_peer.result_sentinel
                    )
                    await assert_no_tool_narration(capture, agent.id)
            finally:
                native_peer.release.set()
