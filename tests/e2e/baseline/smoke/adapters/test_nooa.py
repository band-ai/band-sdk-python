"""NOOA custom tools consume the original Pydantic validation payload."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    ALIASED_LOOKUP_PROMPT,
    ALIASED_LOOKUP_TOOL,
    EXECUTION_REPORTING,
    lookup_code_instruction,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

KEY = "alpha"


class TestNooaValidationAliases:
    @with_adapters(
        Adapter.NOOA,
        tools=[ALIASED_LOOKUP_TOOL],
        prompt=ALIASED_LOOKUP_PROMPT,
        **EXECUTION_REPORTING,
    )
    @pytest.mark.asyncio(loop_scope="session")
    async def test_aliased_payload_round_trips(
        self,
        agent: ProvisionedAgent,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        room_id = await resource_manager.provision_room(
            title="e2e-nooa-validation-alias", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                lookup_code_instruction(KEY),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            replies = await capture.wait_for_reply(mid, agent.id)
            calls = await capture.tool_calls(sender_id=agent.id)

        calls.assert_fired(ALIASED_LOOKUP_TOOL.name, with_args={"projectKey": KEY})
        replies.assert_contains_any([ACCESS_CODES[KEY]])
