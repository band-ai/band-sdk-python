"""Matrix scenario: a message that needs no answer ends the turn through band_no_reply.

The user @mentions the agent with an FYI that says no answer is needed. The agent
declines with ``band_no_reply``: the delivery is PROCESSED, the call is recorded
as a ``tool_call`` event, and nothing reaches the room -- no message and no
missing-reply error.
"""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures, Emit
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.interactions import assert_declined
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

FYI = (
    "FYI only: the nightly deploy finished cleanly. No reply is needed, "
    "please don't answer this."
)


@per_adapter(runs_tool_loop=True, features=AdapterFeatures(emit={Emit.TOOL_CALLS}))
@pytest.mark.asyncio(loop_scope="session")
async def test_an_fyi_ends_the_turn_through_band_no_reply(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    await assert_fyi_decline(agent, resource_manager, user_ops, reply_capture)


async def assert_fyi_decline(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    room_id = await resource_manager.provision_room(
        title=f"e2e-no-reply-{agent.adapter_id}", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id, FYI, mention_id=agent.id, mention_name=agent.name
        )
        await assert_declined(capture, mid, agent)


@per_adapter(
    Adapter.CODEX,
    Adapter.COPILOT_ACP,
    Adapter.CURSOR_ACP,
    Adapter.OMP_ACP,
    features=AdapterFeatures(emit={Emit.TOOL_CALLS}),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_an_fyi_ends_the_turn_through_band_no_reply_coding_backends(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    await assert_fyi_decline(agent, resource_manager, user_ops, reply_capture)
