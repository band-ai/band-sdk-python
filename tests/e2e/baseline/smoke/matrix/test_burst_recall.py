"""Matrix scenario: a burst of N turns is handled with no drop, then recalled across the span.

Combines the two L2 context-fidelity steps into one flow, across the matrix:

* No-drop under load (model-independent): plant N facts in a burst, barrier ONCE on the
  last (per-room FIFO ⟹ every earlier one is handled too), then read each message's
  ``delivery_status`` — every burst turn must reach ``PROCESSED``. This proves each turn
  was *handled*; ``PROCESSED`` does not imply a reply (``capture.py`` warns of this), so
  the reply side rides the recall step below. The sends are awaited sequentially so
  send-order equals server receipt-order — that is what makes the last-message barrier a
  sound proxy for "all earlier handled" (a concurrent ``gather`` has no well-defined
  "last").
* Spanning recall (model-driven): one recall probe, then assert an EARLY, a MID-history,
  and a RECENT fact each *separately* over the recall turn's replies. A single-fact
  recall can't tell "kept the whole history" from "kept only a recent window", and an
  any-of over the three would pass after just one — so the three are asserted apart.

A stronger sibling of ``test_recalls_within_session`` (single fact, two turns). Excludes
``crewai_flow`` (terminal echo, no memory), matching that test; codex/opencode are kept
(in-session recall works — only their cross-run ``/context`` rehydration differs).
"""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_model

from band.client.streaming import DeliveryStatus

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    RECALL_ALL_FACTS,
    REPLY_PROMPT,
    remember_fact_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Enough turns that an early fact tests genuine span, not just a recent window.
BURST_SIZE = 6


@per_adapter(exclude={Adapter.CREWAI_FLOW}, prompt=REPLY_PROMPT)
@flaky_model("spanning recall is model-driven")
@pytest.mark.timeout(
    extra=780
)  # a BURST_SIZE-turn backlog + a recall turn (slow backends)
@pytest.mark.asyncio(loop_scope="session")
async def test_burst_handled_then_spanning_recall(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """No burst turn is dropped, and an early/mid/recent fact all survive to recall."""
    facts = [unique_marker(f"fact{index}") for index in range(BURST_SIZE)]
    room_id = await resource_manager.provision_room(
        title=f"e2e-burst-recall-{agent.adapter_id}", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        # Burst: sequential sends (no processing wait between them) so receipt-order is
        # deterministic and the last-message barrier proves every earlier turn handled.
        mids = [
            await user_ops.send_message(
                room_id,
                remember_fact_instruction(fact),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            for fact in facts
        ]
        # No-drop gate: barrier once on the last, then non-waiting per-message reads.
        # The single barrier covers a BURST_SIZE-turn FIFO backlog, so size its deadline
        # to the backlog (the per-turn default would false-fail on a slow backend).
        await capture.wait_for_processed(
            mids[-1], agent.id, deadline_s=baseline_settings.e2e_timeout * BURST_SIZE
        )
        for fact, mid in zip(facts, mids):
            status = capture.delivery_status(mid, agent.id)
            assert status == DeliveryStatus.PROCESSED, (
                f"burst turn for {fact} was dropped: delivery status {status}"
            )

        # Spanning recall: one probe, then an early / mid / recent fact each separately.
        mark = capture.messages.snapshot()
        recall_mid = await user_ops.send_message(
            room_id, RECALL_ALL_FACTS, mention_id=agent.id, mention_name=agent.name
        )
        recall = await capture.wait_for_reply(recall_mid, agent.id, since=mark)

    recall.assert_contains_any([facts[0]])  # early
    recall.assert_contains_any([facts[BURST_SIZE // 2]])  # mid-history
    recall.assert_contains_any([facts[-1]])  # recent
