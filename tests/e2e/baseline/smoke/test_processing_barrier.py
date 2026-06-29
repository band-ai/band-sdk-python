"""Regression smoke for the delivery-status processing barrier.

Reproduces the failure that motivated the barrier and verifies the fix. The room
processes messages one-at-a-time in FIFO order (a single per-room loop), so a
back-to-back burst lands a backlog in the queue and the barrier on the *last* id
is exercised against an earlier still-in-flight message. ``wait_for_processed``
must report completion off the platform's ``message_updated`` delivery state, so
it is immune to whatever the agent says (or whether it replies at all). The
earlier token-echo approach broke exactly here — it answers conversationally and
drops a "respond with exactly X" instruction, so the echo never appears and the
wait times out (it managed ~1/6 under this shape).

Beyond "the barrier settled", the test asserts the contract that makes waiting on
a single id valid: FIFO transitivity (``last`` processed ⟹ the earlier message is
processed too) and reply-before-processed (the reply is already buffered once the
barrier returns).

Run with:

    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/test_processing_barrier.py -v -s --no-cov
"""

from __future__ import annotations

import logging

import pytest

from band.client.streaming import DeliveryStatus

from tests.e2e.baseline.agents import Adapter, across_adapters
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

logger = logging.getLogger(__name__)


# Repeat so a flaky barrier (the old echo approach managed ~1/6 here) is caught.
ROUNDS = 4


@across_adapters(include={Adapter.ANTHROPIC, Adapter.LANGGRAPH, Adapter.LETTA})
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_barrier_settles_message_burst(
    adapter_id: str,
    matrix_agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    room_id = await resource_manager.provision_room(
        title=f"e2e-barrier-{adapter_id}", participants=[matrix_agent.id]
    )
    mention = {"mention_id": matrix_agent.id, "mention_name": matrix_agent.name}

    async with reply_capture(room_id) as capture:
        for round_no in range(ROUNDS):
            # Cursor at the buffer's end so we can read just this round's replies
            # after the barrier (the capture is reused across rounds).
            mark = capture.messages.snapshot()
            # Burst sent back-to-back without waiting, so both land in the room's
            # FIFO queue before either is processed — the barrier on ``last`` then
            # has to settle against ``first`` still in flight.
            first = await user_ops.send_message(
                room_id, "Remember: my favorite color is teal.", **mention
            )
            last = await user_ops.send_message(
                room_id, "Also remember: my dog is named Pixel.", **mention
            )
            # Resolves from delivery state, not reply text. Raises TimeoutError
            # (failing the test) if the barrier is unreliable.
            await capture.wait_for_processed(last, matrix_agent.id)
            # FIFO transitivity: a processed ``last`` proves the earlier message
            # was processed too — the property that makes waiting on a single id
            # valid. (Checked from already-observed state; no extra wait.)
            assert (
                capture.delivery_status(first, matrix_agent.id)
                == DeliveryStatus.PROCESSED
            ), (
                f"{adapter_id} round {round_no}: {last} processed but earlier {first} "
                f"is {capture.delivery_status(first, matrix_agent.id)} — FIFO broken"
            )
            # Reply-before-processed: PROCESSED is stamped only after the reply is
            # emitted, so a reply for this round is already buffered.
            capture.messages.since(mark).assert_present()
            logger.info(
                "%s round %d: barrier settled on %s", adapter_id, round_no, last
            )
