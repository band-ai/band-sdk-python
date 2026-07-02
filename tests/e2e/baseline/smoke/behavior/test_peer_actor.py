"""A peer agent's message drives a real turn on the agent under test.

This is `PeerActor` used in anger (the L0/L4 `Echo` pattern): a *second
participant* — provisioned but not running any framework adapter — posts a
message mentioning the agent under test, and the agent processes it and echoes
the peer's marker. That proves a peer-authored message reaches the agent's
inference exactly like a user's, which is what the `Echo` bounce relies on, with
no LLM peer or running adapter needed to produce the message.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager


@with_adapters(Adapter.ANTHROPIC, prompt=REPLY_PROMPT)
@pytest.mark.asyncio(loop_scope="session")
async def test_peer_message_drives_agent_turn(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("peer")
    echo = await resource_manager.provision_agent("echo")
    room_id = await resource_manager.provision_room(
        title="e2e-peer-actor", participants=[agent.id, echo.id]
    )

    async with reply_capture(room_id) as capture:
        # The peer (not the user) authors the triggering message.
        mid = await resource_manager.peer(echo).send_message(
            room_id,
            liveness_probe(marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)

    # The *agent under test* (not the peer) echoed the marker the peer asked for →
    # the peer's message drove a real turn (delivered, mention-triggered, reached
    # inference). Scope to the agent's own replies: the peer is itself an Agent, so
    # its marker-bearing trigger is captured too and would falsely satisfy this.
    capture.messages.from_sender(agent.id).assert_contains_any([marker])
