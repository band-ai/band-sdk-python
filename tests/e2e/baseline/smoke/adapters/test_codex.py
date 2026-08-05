"""Codex showcase smokes — adapter-native thought emission on the live backends lane.

The generic matrix runs Codex without ``Emit.THOUGHTS`` (builder features default
to ``None``, so only config-boolean ``TASK_EVENTS`` lands). These smokes turn
thoughts on and assert the room never receives placeholder noise from empty
reasoning/plan items — the live symptom of empty-summary fallbacks posting
``(reasoning)`` / ``(plan)``.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_codex.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures, Emit

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    reasoning_joke_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

PLACEHOLDER_THOUGHTS = ("(reasoning)", "(plan)")


@with_adapters(
    Adapter.CODEX,
    prompt=REPLY_PROMPT,
    features=AdapterFeatures(emit={Emit.THOUGHTS}),
)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.timeout(extra=180)  # Codex cold start + one reasoning turn
@pytest.mark.asyncio(loop_scope="session")
async def test_codex_thoughts_are_not_placeholders(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """With ``Emit.THOUGHTS`` on, empty reasoning/plan items must not spam the room.

    A completed reply proves the turn ran. At least one thought event must have
    landed — otherwise the placeholder assertion below would pass vacuously
    without ever exercising the fix — and none of them may carry the literal
    ``(reasoning)`` / ``(plan)`` placeholders the adapter used to emit for empty
    summaries. The user message uses ``reasoning_joke_instruction`` so
    ``name == marker`` and the ask itself invites reasoning (how a joke might be
    badly interpreted).
    """
    name = unique_marker("Sam")
    room_id = await resource_manager.provision_room(
        title="e2e-codex-thoughts", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            reasoning_joke_instruction(name),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)

    replies.assert_contains_any([name])
    thoughts.assert_at_least(1)
    thoughts.assert_contains_none(PLACEHOLDER_THOUGHTS)
