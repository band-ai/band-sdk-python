from __future__ import annotations

import asyncio

import pytest

from band.core.types import AdapterFeatures
from tests.baseline.adapter import Adapter
from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.activity.kit import HeldLookup, assert_no_tool_narration
from tests.e2e.baseline.smoke.samples.sample_tools import (
    LOOKUP_PROMPT,
    lookup_code_instruction,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestSDKActivity:
    @per_adapter(
        Adapter.ANTHROPIC, prompt=LOOKUP_PROMPT, features=AdapterFeatures(emit=())
    )
    @pytest.mark.asyncio(loop_scope="session")
    @pytest.mark.timeout(extra=120)
    async def test_held_sdk_tool_reports_privately_without_narration(
        self,
        cell: AdapterCell,
        held_lookup: HeldLookup,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        async with cell.running(tools=[held_lookup.spec]) as agent:
            try:
                room_id = await cell.resources.provision_room(
                    title="activity-sdk", participants=[agent.id]
                )
                async with reply_capture(room_id, observe_activity=True) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        f"{lookup_code_instruction('alpha')}. Pass note='{held_lookup.argument_sentinel}' exactly; relay the tool's returned value.",
                        mention_id=agent.id,
                        mention_name=agent.name,
                    )
                    async with asyncio.timeout(cell.settings.e2e_timeout):
                        await held_lookup.entered.wait()
                    await capture.wait_for_tool_activity(
                        agent.id, {held_lookup.spec.name}
                    )
                    mark = capture.activities.snapshot()
                    held_lookup.release.set()
                    replies = await capture.wait_for_reply(mid, agent.id)
                    await capture.wait_for_activity_stop(agent.id, since=mark)
                    replies.assert_contains_any([held_lookup.result_sentinel])
                    assert (
                        held_lookup.arguments[0].note == held_lookup.argument_sentinel
                    )
                    capture.activities.assert_names_only(
                        held_lookup.argument_sentinel, held_lookup.result_sentinel
                    )
                    await assert_no_tool_narration(capture, agent.id)
            finally:
                held_lookup.release.set()
