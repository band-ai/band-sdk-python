"""A room-scoped STOP cannot retire newer work in another room."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures
from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.activitywork import ActivityWork, Operation
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient
from tests.example_agents.anthropic.peer import shared_api

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=12)
PROMPT = "For each request call the requested activity tool afresh, then report its complete opaque result using band_send_message. Earlier results cannot answer a new request."
# NOOA reports its awaited interpreter phase around nested method execution.
NATIVE_ACTIVITY_NAMES = {Adapter.NOOA: "execute_python"}


class TestActivityLifecycle:
    @per_adapter(runs_tool_loop=True)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_old_room_cleanup_preserves_a_fresh_followup(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
    ) -> None:
        identity = await cell.provision()
        first = await cell.resources.provision_room(
            title="e2e-activity-followup", participants=[identity.id]
        )
        second = await cell.resources.provision_room(
            title="e2e-activity-stop", participants=[identity.id]
        )
        observed = ActivityCapture(deadline_s=BUDGET.deadline_s)
        async with shared_api() as (client, base_url, _):
            work = ActivityWork(client, base_url, deadline_s=BUDGET.deadline_s)
            detail = {
                "kind": "tool_call",
                "name": NATIVE_ACTIVITY_NAMES.get(cell.adapter_id, work.tool.name),
            }
            async with (
                observed.rooms(baseline_ws, first, second),
                reply_capture(first, deadline_s=BUDGET.deadline_s) as left,
                reply_capture(second, deadline_s=BUDGET.deadline_s) as right,
                cell.run_as_with_handle(
                    identity,
                    prompt=PROMPT,
                    features=AdapterFeatures(emit=frozenset()),
                    tools=[work.tool],
                    link_factory=observed.link,
                    session_config=SessionConfig(
                        max_working_state_seconds=None, max_cycle_seconds=None
                    ),
                ),
                work.releasing_on_exit(),
            ):
                initial = observed.cursor()
                a = await user_ops.send_message(
                    first,
                    work.instruction(Operation.A),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await work.entered(Operation.A)
                await observed.detail(first, identity.id, detail, since=initial)
                await observed.report(
                    first,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=initial,
                )

                b_phase = observed.cursor()
                await user_ops.send_message(
                    second,
                    work.instruction(Operation.B),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await work.entered(Operation.B)
                await observed.detail(second, identity.id, detail, since=b_phase)
                await observed.report(
                    second,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=b_phase,
                )

                a_finishing = observed.cursor()
                work[Operation.A].release.set()
                await work.settled(Operation.A)
                replies = await left.wait_for_reply(a, identity.id)
                replies.assert_contains_exact(work[Operation.A].result)
                await observed.report(
                    first, agent_id=identity.id, working=False, since=a_finishing
                )
                await observed.stopped(first, identity.id, since=a_finishing)

                c_phase, reply_phase = observed.cursor(), left.messages.snapshot()
                c = await user_ops.send_message(
                    first,
                    work.instruction(Operation.C),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await work.entered(Operation.C)
                await observed.detail(first, identity.id, detail, since=c_phase)
                await observed.report(
                    first,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=c_phase,
                )

                stopping = observed.cursor()
                await user_ops.stop_agent(second)
                control = await observed.control(
                    second, since=stopping, agent_id=identity.id
                )
                work[Operation.B].release.set()
                await work.settled(Operation.B)
                await observed.control_finished(control)
                await observed.report(
                    second, agent_id=identity.id, working=False, since=stopping
                )
                await observed.stopped(second, identity.id, since=stopping)

                survival = observed.cursor()
                await observed.report(
                    first,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=survival,
                )
                c_finishing = observed.cursor()
                work[Operation.C].release.set()
                await work.settled(Operation.C)
                replies = await left.wait_for_reply(c, identity.id, since=reply_phase)
                replies.assert_contains_exact(work[Operation.C].result)
                replies.assert_contains_none(
                    [work[Operation.A].result, work[Operation.B].result]
                )
                await observed.report(
                    first, agent_id=identity.id, working=False, since=c_finishing
                )
                await observed.stopped(first, identity.id, since=c_finishing)
                assert await left.tool_calls(sender_id=identity.id) == []
                assert await right.tool_calls(sender_id=identity.id) == []
                observed.assert_private(
                    work.note,
                    *(invocation.result for invocation in work.operations.values()),
                )
