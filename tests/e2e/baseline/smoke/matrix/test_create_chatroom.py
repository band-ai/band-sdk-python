"""Matrix scenario: an agent creates a new chat room via band_create_chatroom.

Room-creation across the tool-loop matrix. ``band_create_chatroom`` takes no title and no
participants and returns only the new room id, so the round-trip is observed via the
agent's own chat list, not reply text. Two halves prove the *full* round-trip:

* the call — ``band_create_chatroom`` fired (from the persisted ``tool_call`` events);
* the state — exactly one new room appears in the agent's ``list_agent_chats`` between a
  before/after snapshot (the room genuinely landed, not merely a call emitted).

The created room is agent-owned with no human participant, so it is registered with
``resource_manager.adopt_room`` for teardown; it is reaped via the user-scoped delete,
which the platform authorizes because the test user owns the agent that owns the room.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import CREATE_CHATROOM, TOOL_AGENT
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The platform tool under test — a stable public tool name (see CLAUDE.md chat tools).
CREATE_CHATROOM_TOOL = "band_create_chatroom"


@per_adapter(runs_tool_loop=True, **TOOL_AGENT, **EXECUTION_REPORTING)
@pytest.mark.flaky(reruns=2)  # the tool call is a model decision
@pytest.mark.timeout(extra=120)  # one turn, with headroom for a slow backend cold-start
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_creates_a_chatroom(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """band_create_chatroom fires AND a new room actually lands in the agent's chat list."""
    room_id = await resource_manager.provision_room(
        title=f"e2e-create-chatroom-{agent.adapter_id}", participants=[agent.id]
    )
    before = await resource_manager.agent_room_ids(agent)

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id, CREATE_CHATROOM, mention_id=agent.id, mention_name=agent.name
        )
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    # Adopt every new room for teardown reaping BEFORE asserting, so a failing assertion
    # never leaks it (agent-owned; the user-scoped delete is authorized for the owner, and
    # adopt_room is idempotent) — same automatic reap path as a provisioned room.
    new_rooms = await resource_manager.agent_room_ids(agent) - before
    for created in new_rooms:
        resource_manager.adopt_room(created)

    # The call half: the create tool fired.
    calls.assert_fired(CREATE_CHATROOM_TOOL)
    # The state half: exactly one new room landed in the agent's own chat list.
    assert len(new_rooms) == 1, f"expected exactly one new room, got {new_rooms}"
