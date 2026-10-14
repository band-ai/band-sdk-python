"""What the model decides about a batch of queued inputs, and how the batch settles.

With ``consume_all_queued`` on, a few inputs queued behind a held opener turn reach
the model together. The SDK's turn guidance tells it to weigh them as a whole, so:

* an actionable request still gets its answer when an unrelated FYI arrived after
  it, rather than a decline because the last input needs nothing;
* a queue of FYIs that collectively needs no answer ends in one deliberate decline,
  which completes every original (the turn is otherwise reported as FAILED);
* a request followed by its sender's correction is answered as corrected.

Every original in the shared turn ends in the same durable state. The agent runs
under a prompt that only sets its role, so the behavior under test is the SDK's own
guidance; whether a model follows it is what these cells measure. Every adapter
whose class declares ``supports_batch_input`` joins the matrix.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/behavior/test_queued_decisions.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.queuedroom import queued_room
from tests.e2e.baseline.smoke.samples.sample_agents import (
    ASSISTANT_PROMPT,
    FYI_NOTICES,
    correction_probe,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The held opener's turn, then the one shared turn over the queued originals.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=2)


class TestQueuedDecisions:
    @per_adapter(batch_input=True, prompt=ASSISTANT_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_request_is_answered_when_an_fyi_arrived_after_it(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        invocation_observer: InvocationObserver,
    ) -> None:
        marker = unique_marker("request")
        async with queued_room(
            cell,
            owner=user_ops,
            reply_capture=reply_capture,
            observer=invocation_observer,
            budget=BUDGET,
            consume_all_queued=True,
        ) as room:
            batch = await room.queue_texts_while_busy(
                [liveness_probe(marker), FYI_NOTICES[0]]
            )

            assert room.partition == batch.batched_turns
            assert batch.processed == batch.originals
            batch.replies.assert_contains_any([marker])

    @per_adapter(batch_input=True, prompt=ASSISTANT_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_queue_of_fyis_is_declined_once_for_every_original(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        invocation_observer: InvocationObserver,
    ) -> None:
        async with queued_room(
            cell,
            owner=user_ops,
            reply_capture=reply_capture,
            observer=invocation_observer,
            budget=BUDGET,
            consume_all_queued=True,
        ) as room:
            batch = await room.queue_texts_while_busy(list(FYI_NOTICES))

            assert room.partition == batch.batched_turns
            assert batch.processed == batch.originals
            batch.replies.assert_none()

    @per_adapter(batch_input=True, prompt=ASSISTANT_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_request_is_answered_as_its_sender_corrected_it(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        invocation_observer: InvocationObserver,
    ) -> None:
        replaced, corrected = unique_marker("replaced"), unique_marker("corrected")
        async with queued_room(
            cell,
            owner=user_ops,
            reply_capture=reply_capture,
            observer=invocation_observer,
            budget=BUDGET,
            consume_all_queued=True,
        ) as room:
            batch = await room.queue_texts_while_busy(
                [liveness_probe(replaced), correction_probe(corrected)]
            )

            assert room.partition == batch.batched_turns
            assert batch.processed == batch.originals
            batch.replies.assert_contains_any([corrected])
            batch.replies.assert_contains_none([replaced])
