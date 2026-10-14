"""A turn held open in one room does not stall another room, batching on or off.

One agent serves both rooms. Its turn in the first room is parked at its entry, so
that room's message stays in flight; the second room's message must still be admitted,
answered and processed meanwhile. Only then is the first room's turn released and its
message finished. Every adapter whose class declares ``supports_batch_input`` joins
the matrix.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/behavior/test_queued_rooms.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.runtime.types import SessionConfig
from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    LIVENESS_REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The second room's turn, then the first room's released turn.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)


class TestQueuedRooms:
    @per_adapter(batch_input=True, prompt=LIVENESS_REPLY_PROMPT)
    @pytest.mark.parametrize("consume_all_queued", [True, False])
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_turn_held_in_one_room_leaves_the_other_room_progressing(
        self,
        cell: AdapterCell,
        consume_all_queued: bool,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        invocation_observer: InvocationObserver,
    ) -> None:
        marker = unique_marker("independent")
        async with cell.running(
            session_config=SessionConfig(consume_all_queued=consume_all_queued),
            observer=invocation_observer,
        ) as agent:
            held_room, free_room = [
                await resource_manager.provision_room(
                    title=f"e2e-queued-rooms-{cell.adapter_id}-{name}",
                    participants=[agent.id],
                )
                for name in ("held", "free")
            ]
            async with (
                reply_capture(held_room) as held,
                reply_capture(free_room) as free,
            ):
                hold = invocation_observer.hold_next(held_room)
                held_message = await user_ops.send_message(
                    held_room,
                    liveness_probe(unique_marker("held")),
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                await hold.entered()

                free_message = await user_ops.send_message(
                    free_room,
                    liveness_probe(marker),
                    mention_id=agent.id,
                    mention_name=agent.name,
                )
                replies = await free.wait_for_reply(
                    free_message, agent.id, deadline_s=BUDGET.deadline_s
                )
                held_while_free_finished = await held.durable_delivery_statuses(
                    [held_message], agent.id
                )

                hold.release()
                await held.wait_for_processed(
                    held_message, agent.id, deadline_s=BUDGET.deadline_s
                )

        replies.assert_contains_any([marker])
        assert held_while_free_finished == {held_message: DeliveryStatus.PROCESSING}
        assert invocation_observer.partition(held_room) == [(held_message,)]
        assert invocation_observer.partition(free_room) == [(free_message,)]
