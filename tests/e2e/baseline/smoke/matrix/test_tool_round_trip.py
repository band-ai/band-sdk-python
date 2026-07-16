"""Matrix scenario: a custom tool round-trips on every tool-capable adapter.

The same one ``ToolSpec`` (``LOOKUP_TOOL``) is built into each adapter's native
form and driven end to end: the agent must call the opaque tool (it cannot guess
the secret code) and then report that code back in a chat message. Asserting both
halves — the tool fired with the right arg AND its opaque result reached the reply
— proves the *full* round-trip (call dispatch, execution, result-to-model, reply),
not merely that a call was emitted.

The matrix is the tool-loop subgroup, selected by the ``runs_tool_loop`` registry
flag (not a hand-kept adapter list): anthropic-family, langgraph, pydantic-ai,
gemini/google-adk, crewai, agno. Lane scoping shards it across CI jobs (crewai in
its own lane, google in its own), so this one test covers all of them without the
per-lane special-casing the older hardcoded-include tests needed.

Supersedes the older ``test_custom_tool_fires_across_frameworks`` (a hardcoded
three-framework, fired-only check) and the standalone ``test_crewai_tool_fires``:
the flag-driven subgroup covers their adapters and more, and asserts the reply
value on top of the tool firing.
"""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    EXECUTION_REPORTING,
    LOOKUP,
    LOOKUP_PROMPT,
    LOOKUP_TOOL,
    lookup_code_instruction,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

KEY = "alpha"  # ACCESS_CODES["alpha"] -> a code the model cannot guess


@per_adapter(
    runs_tool_loop=True,
    tools=[LOOKUP_TOOL],
    prompt=LOOKUP_PROMPT,
    **EXECUTION_REPORTING,
)
@flaky_infra("only transient failures")
# crewai's crew construction + first kickoff cold-start can push the turn past the
# base budget; grant headroom so a slow-but-healthy cell isn't killed by the timeout.
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_custom_tool_round_trips(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The tool fires with the right arg AND its opaque result lands in the reply."""
    room_id = await resource_manager.provision_room(
        title=f"e2e-tool-roundtrip-{agent.adapter_id}", participants=[agent.id]
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

    # The call half: the opaque tool fired with the requested key.
    calls.assert_fired(LOOKUP, with_args={"key": KEY})
    # The result half: the secret code (knowable only via the tool result) came
    # back in the reply — the round-trip completed, not just the dispatch.
    replies.assert_contains_any([ACCESS_CODES[KEY]])
