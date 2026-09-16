"""A2AAdapter showcase smoke -- a live A2AAdapter driven against
``a2aServer.A2ACounterparty``, a minimal scripted A2A server (not Band's
own gateway), proving the outbound adapter against an independent
implementation. Deterministic, not LLM-backed, so neither side needs an
LLM key.

A2A is a protocol bridge, not an LLM-agent adapter (``NON_AGENT_ADAPTERS``),
so this is a bespoke, non-matrix smoke like ``test_parlant.py``: the
adapter is built directly and handed to ``running_provisioned_agent`` so
provisioning, capture, and reaping share the same plumbing as every other
baseline test.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_a2a.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from band.integrations.a2a import A2AAdapter

from tests.e2e.baseline.agents import Lane, lane
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
from tests.lifecycle import running


# Not in the adapter registry, so the lane selector can't derive a home
# lane and would run it in every lane. Pin to core -- needs no provider key
# (the counterparty is scripted), only the always-on Band-platform gate.
@lane(Lane.CORE)
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
    async with running(A2ACounterparty()) as counterparty:
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

    replies.assert_contains_any([CANNED_REPLY])


@lane(Lane.CORE)
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
    async with running(A2ACounterparty()) as counterparty:
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

    errors.assert_present()
