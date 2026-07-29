"""Matrix scenario: K same-adapter instances co-reside in one room, each replies.

The L3 operational gate — model-light and standalone. ``cell.run_many`` stands up K=3
instances of the current matrix adapter (distinct identities) **concurrently** in one
room; a mention is fired at each, and each must reply. Collisions fail loud for free: an
instance that can't start makes ``run_many`` raise (the test errors); one deadlocked on a
shared port/lock never reaches ``PROCESSED`` (its barrier times out); one that starts but
can't reply times out its ``wait_for_reply`` barrier.

Runs the matrix via ``@per_adapter()`` — **including** codex/opencode, the
shared-``serve`` / shared-``CWD`` backends whose co-residency this gate most needs to
probe. A backend that cannot host three co-resident instances fails loud here (a real L3
conformance signal — "if a second instance cannot start, L3 cannot run"), not a cell to
suppress.

Letta is the one documented exclusion: the Letta server materializes MCP tools
globally **by name** (verified live — three registrations report identical tool
ids), so K instances registering the same band tool surface all route through
the last registrant's server and cross-wire their sends. Co-residency on one
Letta server is a backend modeling constraint, not an adapter bug; lifting it
would need per-instance tool-name suffixes in the self-hosted server.

Concurrency discipline (from the ``ReplyCapture`` contract): the *sends* are independent
REST calls, so they are gathered; the delivery barriers share the capture's single nudge,
so they are awaited **sequentially** — never gathered.
"""

from __future__ import annotations

import asyncio

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from tests.e2e.baseline.agents import Adapter, ExcludedAdapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import liveness_probe, unique_marker
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

INSTANCES = 3  # the spec's Test Agent + Calc + Greeter trio

# One barrier per instance, each awaiting a turn slowed by the other two.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=INSTANCES)


@per_adapter(
    exclude=[
        ExcludedAdapter(
            Adapter.LETTA,
            "global-by-name MCP tools collide across concurrent same-adapter "
            "instances (see module docstring)",
        )
    ]
)
# Diagnosed live via the adapter's turn-phase logs: under K concurrent turns on
# one shared serve + a throttled free model, a session's terminal event
# (session.idle) can arrive AFTER the adapter's 300s turn budget -- the turn is
# aborted with an error and the late reply is lost. That is genuine backend
# non-completion (rerun-worthy infra transience), distinct from the healthy-slow
# case the widened barrier below covers; it is not an adapter bug.
@flaky_infra("shared serve may delay a turn's terminal event past the 300s budget")
@pytest.mark.timeout(extra=BUDGET.extra_s)  # three concurrent boots + three turns
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_same_adapter_instances_each_reply(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """K co-resident instances of one adapter each answer their own mention."""
    async with cell.run_many(INSTANCES) as instances:
        room_id = await resource_manager.provision_room(
            title=f"e2e-concurrent-{cell.adapter_id}",
            participants=[instance.id for instance in instances],
        )
        async with reply_capture(room_id) as capture:
            # Gather the SENDS (independent REST calls)...
            mids = await asyncio.gather(
                *(
                    user_ops.send_message(
                        room_id,
                        liveness_probe(unique_marker("hi")),
                        mention_id=instance.id,
                        mention_name=instance.name,
                    )
                    for instance in instances
                )
            )
            # ...but await the barriers SEQUENTIALLY (one nudge per capture). Each
            # wait_for_reply blocks until that instance's own reply is captured, or
            # raises TimeoutError naming the stalled turn — so completing this loop is
            # itself the proof that all K instances co-resided and each replied.
            #
            # Widen the per-reply deadline: this fires K turns at once, and the
            # backend lanes (codex/opencode) funnel them through one shared serve
            # against a throttled free model, so a healthy turn can legitimately
            # take longer than the default single-turn deadline, and the default
            # tripped the barrier on a turn that was still running (spurious "no
            # reply"). ``slow_turn_budget`` sizes each barrier at the adapters' own
            # turn budget -- past which no reply can still be coming -- and sets the
            # marker's ``extra`` above all K, so a genuine stall reports the stalled
            # turn rather than a phase-less pytest-timeout kill. Fast adapters reply
            # well within this: it raises only the failure ceiling, never latency.
            for instance, mid in zip(instances, mids):
                await capture.wait_for_reply(
                    mid, instance.id, deadline_s=BUDGET.deadline_s
                )
