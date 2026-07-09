"""Token-usage smokes for the baseline toolkit (Emit.USAGE seam).

The cross-adapter proof for the cost/token seam: an agent running with
``Emit.USAGE`` emits its per-turn token usage, read back via
``ReplyCapture.usage`` and asserted with :class:`Usage`. This is the end-to-end
de-risking test for the ``Emit.USAGE`` / ``capture.usage()`` design across every
usage-capable adapter.

Coverage is registry-derived, not a hand-maintained list: the fan is the whole
matrix minus the adapters that don't emit usage — ``CREWAI_FLOW`` (usage lives in
user-supplied flow internals — N-A) and ``CREWAI`` (usage capture deferred: its
result counter is cumulative-lifetime, not per-turn). ``LETTA`` is auto-excluded
because it is ``e2e_pending`` (it captures usage too, covered by unit mapping
tests). Deriving from ``exclude=`` rather than an explicit include-list means a
newly-registered usage-capable adapter is exercised automatically — and a new
adapter that *cannot* emit usage fails loudly here until it's consciously added to
the exclusion, which is the intended signal. The cells span several CI lanes
(core / google / backends); each is a single-adapter ``@per_adapter`` item, so
each runs in its own lane's job — no ``@lane`` pin needed.

Turn completion uses the delivery-status barrier (``wait_for_processed``):
``processed`` is stamped when the handler completes, by which point the turn's
usage event is persisted — so the read is race-free.

``test_usage_not_cumulative_across_turns`` extends the same fan to two turns and
asserts each turn emits its *own* record scoped to that turn — the live guard for
the per-turn convention. It catches the class of bug found in review where an
adapter reports a *cumulative* count (a run/session total that grows every turn,
e.g. pydantic_ai's benign fallback summing the replayed history, or crewai's
lifetime ``usage_metrics``). It drives one long turn then one one-word turn: a
correct per-turn second record sits far below the long turn, while a cumulative
one (long + tiny) rises to ~= the long turn — a scale-immune split that ordinary
reply-length variance can't cross.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    COST_AGENT,
    COST_MULTI_TURN_AGENT,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


# Adapters that don't emit usage: crewai_flow (usage in user-supplied flow
# internals) and crewai (deferred — cumulative-lifetime counter, not per-turn).
# Every other registered adapter must emit usage. (LETTA is auto-excluded as
# e2e_pending — covered by unit mapping tests.)
@per_adapter(exclude={Adapter.CREWAI_FLOW, Adapter.CREWAI}, **COST_AGENT)
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
      realistic ceiling. The floor sums input + cache-read + cache-write because
      TurnUsage fields are the provider's *raw* values and whether cache is
      already inside ``input_tokens`` is provider-specific: caching adapters
      (e.g. claude_sdk) report ~7 fresh ``input_tokens`` with the bulk (~87k) under
      cache_read, so an ``input_tokens``-only floor would be wrong. The sum is a
      robust lower bound on prompt size either way. A rendered system prompt +
      tool schemas puts it in the hundreds+, so 20 catches a garbage/tiny count a
      bare ``> 0`` would pass; a one-line reply keeps ``total_tokens`` under the
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
    # Estimation: a realistic count, not just > 0. Sum the prompt the model
    # processed — fresh input + cache-read + cache-write — because TurnUsage
    # fields are raw provider values and caching adapters report most of the
    # prompt under cache_* with only a few fresh input_tokens. The sum is a
    # robust lower bound on prompt size regardless of whether the provider counts
    # cache inside input; a rendered system prompt + tool schemas puts it well
    # above 20, and a one-line reply keeps input+output under the ceiling.
    record = usage[0]
    prompt_tokens = (
        record.input_tokens + record.cache_read_tokens + record.cache_write_tokens
    )
    assert prompt_tokens >= 20, (
        f"prompt tokens implausibly low for a real turn: {prompt_tokens} "
        f"(input={record.input_tokens}, cache_read={record.cache_read_tokens}, "
        f"cache_write={record.cache_write_tokens})"
    )
    assert record.total_tokens < 100_000, (
        f"total tokens implausibly high for a one-line reply: {record.total_tokens}"
    )


# Same fan and exclusions as the single-turn smoke: every usage-emitting adapter,
# minus the crewai pair (crewai_flow N-A; crewai deferred). LETTA auto-excluded
# (e2e_pending). The prompt lets the user dictate length so the test can drive one
# LONG turn then one TINY turn (see COST_MULTI_TURN_AGENT).
@per_adapter(exclude={Adapter.CREWAI_FLOW, Adapter.CREWAI}, **COST_MULTI_TURN_AGENT)
# Two turns, and the first drives a long multi-paragraph generation — so this test
# legitimately needs roughly a second turn's budget on top of the per-turn default.
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_usage_not_cumulative_across_turns(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each turn emits its OWN usage record — a per-turn count, never a cumulative total.

    The live guard for the per-turn convention. Review found two adapters that
    reported a *cumulative* count — a run/session total that grows every turn
    (pydantic_ai's benign fallback summed the replayed history; crewai's
    ``usage_metrics`` is a lifetime counter). This drives two turns in one room and
    checks the second turn's record is that turn alone, not turn1+turn2.

    The turns are deliberately ASYMMETRIC — a long first turn, a one-word second
    turn — because that makes the check immune to LLM reply-length variance. Two
    equal turns would force a fragile "1x vs 2x" ratio whose margin collapses when
    an ordinary turn runs a bit long; instead:

    - a correct per-turn second record is the *tiny* turn alone → far BELOW the
      long first turn's output;
    - a cumulative second record is ``long + tiny`` → ``~=`` the long first turn.

    So ``turn2.output < turn1.output`` cleanly separates them (asserted at half,
    for headroom). Output, not input: history grows turn-over-turn, so input rises
    even when correct — output is the clean per-turn discriminator, and it's
    exactly what a cumulative bug inflates. Two tolerant checks:

    - exactly two records, one per turn — a per-turn emitter produces one record
      each turn; a double-emit or a per-run-instead-of-per-turn regression would
      change the count. (Same reasoning as the single-turn smoke's ``len == 1``.)
    - the second record is not cumulative (the ratio above), floored by a plausible
      first-turn size so the ratio can't pass vacuously on a degenerate record.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-usage-not-cumulative", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        first = await user_ops.send_message(
            room_id,
            "Explain in detail how ocean tides work. Write several full paragraphs.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(first, agent.id)
        second = await user_ops.send_message(
            room_id,
            "Reply with a single word: yes.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(second, agent.id)
        # Both turns are complete and persisted; read the whole room's usage,
        # oldest-first — [0] is turn 1 (long), [1] is turn 2 (one word).
        usage = await capture.usage(sender_id=agent.id)

    # Baseline sanity: real usage was recorded on both turns (input from the
    # prompt, output from the replies) — reuses the L4 gate.
    usage.assert_nonzero_input_and_output()
    assert len(usage) == 2, (
        f"expected exactly one usage record per turn (two turns), got {usage}"
    )
    turn_one, turn_two = usage[0], usage[1]
    # The long turn must actually be sizeable, else the ratio below is vacuous
    # (a degenerate near-zero turn_one would let almost anything pass).
    assert turn_one.output_tokens >= 20, (
        f"first turn's output implausibly small for a multi-paragraph reply: "
        f"{turn_one.output_tokens}"
    )
    # The non-cumulative discriminator: a per-turn record for the one-word turn is
    # far below the long turn's output, while a cumulative one (long + tiny) is
    # ~= the long turn. Half the long turn splits them with wide margin either way.
    assert turn_two.output_tokens < turn_one.output_tokens * 0.5, (
        "second turn's output looks cumulative (turn1+turn2), not per-turn: a "
        f"one-word reply should be far below the long turn — turn1="
        f"{turn_one.output_tokens}, turn2={turn_two.output_tokens}"
    )
