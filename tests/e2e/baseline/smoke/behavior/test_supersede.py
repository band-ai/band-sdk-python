"""Live same-key supersede coverage for ``ConflictPolicy.SUPERSEDE``.

A newer session for an agent id evicts the running one with a terminal
``supersede`` and a 1001 close. The evicted agent must end its session for
good -- a reconnect would evict the newer session in turn -- while the newer
one keeps serving. Only the live platform produces the real push/close
ordering. Single fixed adapter: this is a transport concern, not
adapter-specific (matches test_duplicate_start.py).
"""

from __future__ import annotations

import asyncio

import pytest

from band import AgentConfig
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
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

SUPERSEDED_REASON = "session.already_connected"


@per_adapter(Adapter.ANTHROPIC, prompt=REPLY_PROMPT)
@pytest.mark.timeout(extra=60)
@pytest.mark.asyncio(loop_scope="session")
async def test_a_newer_session_ends_the_running_one_for_good(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """The newer session runs through ``running_agent_with_handle`` rather than
    the cell: the cell's ``track_running`` guard exists to forbid this overlap.

    ``run_forever`` returning proves the evicted transport has shut down, so
    no later reconnect can follow; the newer session's reply then proves it
    was never evicted back.
    """
    identity = await cell.provision(label=f"supersede-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-supersede-{cell.adapter_id}", participants=[identity.id]
    )

    async with cell.run_as_with_handle(identity) as incumbent:
        evicted = incumbent.runtime.link
        async with running_agent_with_handle(
            identity,
            cell.build(),
            cell.settings,
            config=AgentConfig(single_instance=False),
        ) as newer:
            await asyncio.wait_for(
                evicted.run_forever(install_signal_handlers=False),
                baseline_settings.e2e_timeout,
            )

            assert evicted.last_disconnect_reason is not None
            assert evicted.last_disconnect_reason.reason == SUPERSEDED_REASON

            marker = unique_marker("newer")
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

            assert newer.runtime.link.last_disconnect_reason is None
