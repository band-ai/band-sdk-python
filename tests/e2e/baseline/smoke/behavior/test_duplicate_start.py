"""Live duplicate-start coverage for ``ConflictPolicy.REJECT``.

A second start of an agent id that is already connected must be refused by the
platform itself, and the running agent must be left alone. The host lock is
switched off for the second start so it is the platform's refusal that is
exercised, not the local guard's. Single fixed adapter: this is a transport
concern, not adapter-specific (matches test_reconnect.py).
"""

from __future__ import annotations

import pytest

from band import AgentAlreadyRunningError, AgentConfig, ConflictPolicy
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ResourceManager,
    running_agent_with_handle,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(Adapter.ANTHROPIC, prompt=REPLY_PROMPT)
@pytest.mark.timeout(extra=60)
@pytest.mark.asyncio(loop_scope="session")
async def test_second_start_is_refused_and_the_incumbent_keeps_replying(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The duplicate runs through ``running_agent_with_handle`` rather than the
    cell: the cell's ``track_running`` guard exists to forbid this overlap."""
    identity = await cell.provision(label=f"duplicate-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-duplicate-{cell.adapter_id}", participants=[identity.id]
    )

    async with cell.run_as(identity):
        with pytest.raises(AgentAlreadyRunningError, match=identity.id):
            async with running_agent_with_handle(
                identity,
                cell.build(),
                cell.settings,
                config=AgentConfig(
                    single_instance=False, conflict_policy=ConflictPolicy.REJECT
                ),
            ):
                pass

        marker = unique_marker("incumbent")
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                liveness_probe(marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(mid, identity.id, since=mark)
            replies.assert_contains_any([marker])
