"""Regression smoke for the delivery-status processing barrier.

Reproduces the failure that motivated the barrier and verifies the fix. Several
messages are sent back-to-back so the adapter batches them into one turn, then
``wait_for_processed`` must still report completion. The earlier token-echo
approach failed exactly here: a batched turn answers conversationally and drops
the "respond with exactly X" instruction, so the echo never appears and the wait
times out (it managed ~1/6 under this shape). ``wait_for_processed`` keys off the
platform's ``message_updated`` delivery state for the barrier message itself, so
it is immune to whatever the agent says (or whether it replies at all).

Run with:

    E2E_TESTS_ENABLED=true uv run pytest \
        tests/e2e/baseline/smoke/test_processing_barrier.py -v -s --no-cov
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.capture import ReplyCapture

logger = logging.getLogger(__name__)

CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]

# Repeat so a flaky barrier (the old echo approach managed ~1/6 here) is caught.
ROUNDS = 4


@pytest.mark.parametrize("adapter_name", ["anthropic", "langgraph"])
@pytest.mark.asyncio(loop_scope="session")
async def test_barrier_settles_message_burst(
    adapter_name: str,
    request: pytest.FixtureRequest,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    adapter: SimpleAdapter = request.getfixturevalue(f"{adapter_name}_adapter")
    async with running_provisioned_agent(
        adapter, resource_manager, label=adapter_name
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title=f"e2e-barrier-{adapter_name}", participants=[agent.id]
        )
        mention = {"mention_id": agent.id, "mention_name": agent.name}

        async with reply_capture(room_id) as capture:
            for round_no in range(ROUNDS):
                # Burst sent without waiting, so the adapter batches it into one
                # turn — the exact shape that broke the reply-text approach.
                await user_ops.send_message(
                    room_id, "Remember: my favorite color is teal.", **mention
                )
                last = await user_ops.send_message(
                    room_id, "Also remember: my dog is named Pixel.", **mention
                )
                # Must resolve from delivery state, not reply text. Raises
                # TimeoutError (failing the test) if the barrier is unreliable.
                await capture.wait_for_processed(last, agent.id)
                logger.info(
                    "%s round %d: barrier settled on %s", adapter_name, round_no, last
                )
