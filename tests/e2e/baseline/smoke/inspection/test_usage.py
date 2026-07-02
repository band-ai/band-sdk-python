"""Token-usage smokes for the baseline toolkit (Emit.USAGE seam).

The cross-adapter proof for the cost/token seam: an agent running with
``Emit.USAGE`` emits its per-turn token usage, read back via
``ReplyCapture.usage`` and asserted with :class:`Usage`. This is the end-to-end
de-risking test for the ``Emit.USAGE`` / ``capture.usage()`` design across every
usage-capable adapter.

Coverage is registry-derived, not a hand-maintained list: the fan is the whole
matrix minus ``CREWAI_FLOW`` (usage lives in user-supplied flow internals — N-A).
``LETTA`` is auto-excluded because it is ``e2e_pending`` (it captures usage too,
covered by unit mapping tests). Deriving from ``exclude=`` rather than an explicit
include-list means a newly-registered usage-capable adapter is exercised
automatically — and a new adapter that *cannot* emit usage fails loudly here until
it's consciously added to the exclusion, which is the intended signal. The cells
span several CI lanes (core / google / crewai / backends); each is a single-adapter
``@per_adapter`` item, so each runs in its own lane's job — no ``@lane`` pin needed.

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


# crewai_flow is the one usage-incapable matrix adapter (usage lives in
# user-supplied flow internals, not on the result the adapter sees), so exclude
# it; every other registered adapter must emit usage. (LETTA is auto-excluded as
# e2e_pending — covered by unit mapping tests.)
@per_adapter(exclude={Adapter.CREWAI_FLOW}, **COST_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_usage_recorded_for_a_turn(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The proof: one turn emits exactly one usage record with a plausible count.

    Three tolerant, deterministic checks (a floor and a broad band, never exact
    magnitudes, so no LLM-variance flakiness):

    - ``assert_nonzero_input_and_output`` — input tokens > 0 (the prompt was
      sent) AND output tokens > 0 (a reply was generated); the same gate L4
      reuses, here on an ordinary turn.
    - exactly one record — one user message → one agent turn → the adapter sums
      per-call usage into a single ``TurnUsage`` emitted once. Summing across
      records would hide a double-emit or a per-call-instead-of-per-turn
      regression, so assert the count too. The prompt (``COST_AGENT``) uses no
      tools, so the turn is a single model call.
    - a plausible count (estimation) — the total *prompt* tokens the model
      processed clear a realistic floor, and the reply total stays under a
      realistic ceiling. The prompt floor sums input + cache-read + cache-write
      on purpose: caching adapters (e.g. claude_sdk) report most of the prompt
      under cache_read and only a handful of *fresh* ``input_tokens`` (7, with
      ~87k cached, in practice), so a floor on ``input_tokens`` alone would be
      wrong. Every adapter sends a rendered system prompt + tool schemas, so the
      processed prompt is realistically in the hundreds+; 20 sits well below that
      yet still catches a garbage/tiny count a bare ``> 0`` would pass. A
      one-line reply keeps ``total_tokens`` (input + output) far under the
      ceiling. Both bounds stay loose enough that model/run variance never trips
      them. (Exact per-call summing is proven deterministically in the adapter
      unit tests, which — unlike this live read — can see the per-call
      intermediates.)
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
    assert len(usage) == 1, (
        f"expected exactly one usage record for one turn, got {usage}"
    )
    # Estimation: a realistic count, not just > 0. Per the TurnUsage convention
    # input_tokens is the total prompt the model processed (cache folded in by
    # each adapter's mapper), so a rendered system prompt + tool schemas puts it
    # in the hundreds+ across every adapter, caching or not. 20 is a safe floor
    # that still catches a garbage/tiny count; a one-line reply keeps input+output
    # under the ceiling. Both bounds are loose enough that variance never trips them.
    record = usage[0]
    assert record.input_tokens >= 20, (
        "input tokens (total prompt, cache incl.) implausibly low for a real turn: "
        f"{record.input_tokens}"
    )
    assert record.total_tokens < 100_000, (
        f"total tokens implausibly high for a one-line reply: {record.total_tokens}"
    )
