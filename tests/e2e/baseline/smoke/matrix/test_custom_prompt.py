"""Matrix scenario: a custom system prompt takes effect and coexists with platform tools.

Across the tool-loop matrix, the agent runs under a custom prompt that (a) keeps the
opaque-lookup behaviour and (b) requires a marker word in *every* reply. Two turns
prove the prompt is in force and that the custom tool and platform tools coexist under
it:

* Turn 1 (custom tool): look up an opaque access code. The code (knowable only via the
  tool result) AND the marker must both appear — asserted separately over the same
  turn-scoped replies, since an any-of would green on just one.
* Turn 2 (platform tool): ask who is in the room. A known-named room member must appear
  (via band_get_participants) AND the marker persists — again separate assertions —
  plus a known invitable-but-absent peer must NOT be fabricated (a tolerant negative).

The marker is a high-entropy token baked into the prompt at import; the reply capture is
fresh each run, so per-run uniqueness isn't needed. This deliberately does
not re-assert the tool-call round-trip that ``test_tool_round_trip`` owns; it asserts
the *prompt effect* and *coexistence* on top.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    custom_prompt_with_marker,
    unique_marker,
)
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    LOOKUP_TOOL,
    lookup_code_instruction,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Baked into the prompt at import (a fresh capture per run makes per-run uniqueness
# unnecessary); the marker word every reply must carry proves the prompt is in force.
PROMPT_MARKER = unique_marker("marker")
KEY = "beta"  # ACCESS_CODES["beta"] -> a code the model cannot guess


@per_adapter(
    runs_tool_loop=True,
    tools=[LOOKUP_TOOL],
    prompt=custom_prompt_with_marker(PROMPT_MARKER),
)
@pytest.mark.flaky(reruns=2)  # marker-in-every-reply is a model-driven behaviour
@pytest.mark.timeout(extra=180)  # a custom-tool turn, then a roster turn
@pytest.mark.asyncio(loop_scope="session")
async def test_custom_prompt_takes_effect_and_coexists_with_platform_tools(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The prompt marker rides every reply, while custom and platform tools both work."""
    member = await resource_manager.provision_agent("member")
    nonmember = await resource_manager.provision_agent("nonmember")
    room_id = await resource_manager.provision_room(
        title=f"e2e-custom-prompt-{agent.adapter_id}",
        participants=[agent.id, member.id],
    )

    async with reply_capture(room_id) as capture:
        # Turn 1: opaque lookup — the code (tool result) and the marker (prompt) land.
        code_mid = await user_ops.send_message(
            room_id,
            lookup_code_instruction(KEY),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        turn_one = await capture.wait_for_reply(code_mid, agent.id, sender_id=agent.id)
        turn_one.assert_contains_any([ACCESS_CODES[KEY]])  # tool result round-tripped
        turn_one.assert_contains_any([PROMPT_MARKER])  # prompt still in effect

        # Turn 2: roster question — a known member appears, the marker persists, and a
        # known invitable-but-absent peer is not fabricated.
        mark = capture.messages.snapshot()
        who_mid = await user_ops.send_message(
            room_id,
            "Who is currently in this room? Name them.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        turn_two = await capture.wait_for_reply(
            who_mid, agent.id, sender_id=agent.id, since=mark
        )

    # The platform tool still works under the custom prompt (a known member appears),
    # the marker still rides the reply, and an invitable-but-absent peer is not fabricated.
    turn_two.assert_contains_any([member.name])
    turn_two.assert_contains_any([PROMPT_MARKER])
    turn_two.assert_contains_none([nonmember.name])
