"""Messages that queue behind a busy turn: one shared invocation when opted in, one each otherwise.

A hold parks the agent's first turn while the room is already committed to it, so
the messages sent meanwhile queue behind it deterministically -- no sleeps, no timing
window. A second hold parks the next turn, and one more message is sent while it is
open. What the agent was handed is read from its own turns
(``toolkit/invocations.py``), never inferred from how many replies came back, and each
original's delivery state is read durably from the platform. Every adapter whose class
declares ``supports_batch_input`` joins the matrix.

With ``consume_all_queued`` on, the originals queued behind the opener reach one
invocation together, in order, whoever sent them; the message sent after that
invocation began waits for a later one; every original ends processed; and the
replies address every sender. With it off, the same queue is handed over one original
per invocation, and only the original being worked on is admitted.

Two senders are covered twice: a second human (needs ``BAND_API_KEY_USER_2``) and a
peer agent that posts through its own key.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/behavior/test_queued_messages.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.queuedroom import (
    QUEUED_COUNT,
    TURN_BARRIERS,
    queued_room,
)
from tests.e2e.baseline.smoke.samples.sample_agents import LIVENESS_REPLY_PROMPT
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=TURN_BARRIERS)


class TestQueuedMessages:
    @per_adapter(batch_input=True, prompt=LIVENESS_REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_queued_originals_share_one_invocation(
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
            run = await room.queue_while_busy([room.owner] * QUEUED_COUNT)

            assert run.admitted == run.queued
            assert room.partition == run.batched_turns
            assert run.processed == run.originals
            run.assert_every_request_answered()
            run.replies.assert_mentions(room.owner.id)

    @per_adapter(batch_input=True, prompt=LIVENESS_REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_originals_queued_by_two_senders_share_one_invocation_and_both_are_answered(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        second_user_ops: UserOps,
        reply_capture: CaptureFactory,
        invocation_observer: InvocationObserver,
    ) -> None:
        async with queued_room(
            cell,
            owner=user_ops,
            guests=[second_user_ops],
            reply_capture=reply_capture,
            observer=invocation_observer,
            budget=BUDGET,
            consume_all_queued=True,
        ) as room:
            owner, guest = room.senders
            run = await room.queue_while_busy([owner, guest, owner])

            assert run.admitted == run.queued
            assert room.partition == run.batched_turns
            assert run.processed == run.originals
            run.assert_every_request_answered()
            run.replies.assert_mentions(owner.id)
            run.replies.assert_mentions(guest.id)

    @per_adapter(batch_input=True, prompt=LIVENESS_REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_originals_queued_by_a_user_and_an_agent_share_one_invocation_and_both_are_answered(
        self,
        cell: AdapterCell,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        invocation_observer: InvocationObserver,
    ) -> None:
        peer = await resource_manager.provision_agent("sender")
        async with queued_room(
            cell,
            owner=user_ops,
            peers=[peer],
            reply_capture=reply_capture,
            observer=invocation_observer,
            budget=BUDGET,
            consume_all_queued=True,
        ) as room:
            owner, agent_sender = room.senders
            run = await room.queue_while_busy([owner, agent_sender, owner])

            assert run.admitted == run.queued
            assert room.partition == run.batched_turns
            assert run.processed == run.originals
            run.assert_every_request_answered()
            run.replies.assert_mentions(owner.id)
            run.replies.assert_mentions(agent_sender.id)

    @per_adapter(batch_input=True, prompt=LIVENESS_REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_without_opt_in_each_queued_original_gets_its_own_invocation(
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
            consume_all_queued=False,
        ) as room:
            run = await room.queue_while_busy([room.owner] * QUEUED_COUNT)

            assert run.admitted == run.queued[:1]
            assert room.partition == run.singleton_turns
            assert run.processed == run.originals
