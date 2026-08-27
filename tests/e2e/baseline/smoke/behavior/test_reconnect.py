"""Live reconnect-behavior coverage for BandLink's SubscriptionTracker adoption.

Forces a real WebSocket transport disconnect (not the clean disconnect()/
connect() lifecycle) so the transport's own reconnect logic and
BandLink._on_reconnected run for real against the live platform — the part a
mocked-transport unit test can't prove. Single fixed adapter: this is a
transport-layer concern, not adapter-specific, so it doesn't need the full
matrix (matches test_isolation.py / test_processing_barrier.py precedent).
"""

from __future__ import annotations

import logging

import pytest
from band.testing import force_transport_disconnect

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(Adapter.ANTHROPIC, prompt=REPLY_PROMPT)
@pytest.mark.timeout(extra=60)
@pytest.mark.asyncio(loop_scope="session")
async def test_room_survives_real_transport_reconnect(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real socket drop mid-session must not strand the room: the transport's
    own reconnect plus BandLink._on_reconnected must leave it fully usable.

    Requests ``cell.run_as_with_handle`` (not the managed ``agent`` fixture) so
    the test can reach the running agent's own transport via
    ``agent.runtime.link`` — both already-public properties — to force the
    disconnect.
    """
    caplog.set_level(logging.INFO, logger="band.platform.link")

    identity = await cell.provision(label=f"reconnect-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-reconnect-{cell.adapter_id}", participants=[identity.id]
    )

    async with cell.run_as_with_handle(identity) as agent:
        before_marker = unique_marker("before")
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                liveness_probe(before_marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(mid, identity.id, since=mark)
            replies.assert_contains_any([before_marker])

        await force_transport_disconnect(agent.runtime.link)

        after_marker = unique_marker("after")
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                liveness_probe(after_marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            # No sleep, no separate reconnect wait: reply delivery is only
            # possible once the room's channels are live again, so this
            # barrier's own bounded wait is the deterministic proof.
            replies = await capture.wait_for_reply(mid, identity.id, since=mark)
            replies.assert_contains_any([after_marker])

    assert "WebSocket reconnected" in caplog.text
