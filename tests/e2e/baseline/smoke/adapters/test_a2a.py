"""A2AAdapter showcase smoke -- a live A2AAdapter driven against a real,
independent A2A counterparty (not Band's own gateway).

A2A is a protocol bridge, not an LLM-agent adapter (listed in
``NON_AGENT_ADAPTERS``), so this is a bespoke, non-matrix smoke like
``test_parlant.py``: the adapter is built directly and handed to the
toolkit's ``running_provisioned_agent`` so provisioning, capture, and reaping
share the same plumbing as every other baseline test.

The counterparty is ``a2aServer.A2ACounterparty``: a minimal, scripted A2A
server built on a2a-sdk's own primitives (not Band), so this proves the
outbound ``A2AAdapter`` against a real, independent A2A implementation --
not just our own gateway. It is deterministic, not LLM-backed, so neither
side of this smoke needs an LLM key.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_a2a.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from band.integrations.a2a import A2AAdapter

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.a2aServer import (
    CANNED_REPLY,
    ERROR_MARKER,
    A2ACounterparty,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


# A2A isn't in the adapter registry (NON_AGENT_ADAPTERS), so the lane selector
# can't derive its home lane and would run it in every lane. Pin it to core --
# this smoke needs no provider key (the counterparty is scripted, not
# LLM-backed), only the always-on Band-platform gate.
@lane(Lane.CORE)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.timeout(extra=60)
@pytest.mark.asyncio(loop_scope="session")
async def test_a2a_adapter_relays_a_real_counterparty_reply(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A live ``A2AAdapter`` forwards a Band room message to a real,
    independent A2A server and relays its reply back into the room."""
    counterparty = A2ACounterparty()
    await counterparty.start()
    try:
        adapter = A2AAdapter(remote_url=counterparty.url, streaming=True)
        async with running_provisioned_agent(
            adapter, resource_manager, label="a2a"
        ) as agent:
            room_id = await resource_manager.provision_room(
                title="e2e-a2a-reply", participants=[agent.id]
            )
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    "Please say hello.",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await capture.wait_for_reply(
                    mid, agent.id, deadline_s=baseline_settings.e2e_timeout
                )
    finally:
        await counterparty.stop()

    replies.assert_contains_any([CANNED_REPLY])


@lane(Lane.CORE)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.timeout(extra=60)
@pytest.mark.asyncio(loop_scope="session")
async def test_a2a_adapter_surfaces_a_remote_task_failure(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A terminal FAILED task from the remote A2A server surfaces as a room
    error event, not a silently dropped turn."""
    counterparty = A2ACounterparty()
    await counterparty.start()
    try:
        adapter = A2AAdapter(remote_url=counterparty.url, streaming=True)
        async with running_provisioned_agent(
            adapter, resource_manager, label="a2a"
        ) as agent:
            room_id = await resource_manager.provision_room(
                title="e2e-a2a-failure", participants=[agent.id]
            )
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    f"trigger a scripted failure: {ERROR_MARKER}",
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                await capture.wait_for_processed(
                    mid, agent.id, deadline_s=baseline_settings.e2e_timeout
                )
                errors = await capture.errors(sender_id=agent.id)
    finally:
        await counterparty.stop()

    errors.assert_present()
