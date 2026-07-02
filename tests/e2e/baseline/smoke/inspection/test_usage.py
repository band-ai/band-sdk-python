"""Token-usage smokes for the baseline toolkit (Emit.USAGE seam).

The cross-adapter proof for the cost/token seam: an agent running with
``Emit.USAGE`` emits its per-turn token usage, read back via
``ReplyCapture.usage`` and asserted with :class:`Usage`. This is the end-to-end
de-risking test for the ``Emit.USAGE`` / ``capture.usage()`` design as it is
templated across the bucket-B adapters (anthropic, langgraph, pydantic_ai,
claude_sdk, agno in the CORE lane; google_adk and gemini in the google lane;
crewai in the crewai lane; opencode in the backends lane). Letta captures usage
too but is E2E-pending, so it's covered by unit mapping tests, not this smoke.

Turn completion uses the delivery-status barrier (``wait_for_processed``): the
platform marks the trigger ``processed`` only after the reply is emitted, by which
point the turn's usage event is persisted — so the read is race-free.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import COST_AGENT
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(
    Adapter.ANTHROPIC,
    Adapter.LANGGRAPH,
    Adapter.PYDANTIC_AI,
    Adapter.CLAUDE_SDK,
    Adapter.AGNO,
    Adapter.GOOGLE_ADK,
    Adapter.GEMINI,
    Adapter.CREWAI,
    Adapter.OPENCODE,
    **COST_AGENT,
)
@pytest.mark.asyncio(loop_scope="session")
async def test_usage_recorded_for_a_turn(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The proof: one turn emits a usage record with non-zero input and output.

    Non-zero input tokens (the prompt was sent) AND non-zero output tokens (a
    reply was generated) is exactly the ``assert_nonzero_input_and_output``
    gate reused by L4 — here on an ordinary turn.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-usage-recorded", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "Say hello in one short sentence.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        usage = await capture.usage(sender_id=agent.id)

    usage.assert_nonzero_input_and_output()
