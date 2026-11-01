"""Stopped Agno awaiters retain real synchronous-body cleanup without stopping a peer room."""

from __future__ import annotations

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.activitywork import Operation
from tests.e2e.baseline.toolkit.agnowork import (
    WORKER_BUDGET,
    AgnoWorkerWork,
    NativeBodies,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    running_agent_with_handle,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient


class TestAgnoActivity:
    @per_adapter(Adapter.AGNO)
    @pytest.mark.timeout(extra=WORKER_BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_sync_body_settlement_preserves_newer_room_activity(
        self,
        cell: AdapterCell,
        agno_worker_work: AgnoWorkerWork,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
    ) -> None:
        work = agno_worker_work
        identity = await cell.provision()
        old_room = await cell.resources.provision_room(
            title="e2e-agno-worker-stop", participants=[identity.id]
        )
        new_room = await cell.resources.provision_room(
            title="e2e-agno-worker-peer", participants=[identity.id]
        )
        observed = ActivityCapture(deadline_s=WORKER_BUDGET.deadline_s)
        bodies = NativeBodies(deadline_s=WORKER_BUDGET.deadline_s)
        detail = {"kind": "tool_call", "name": work.name}
        adapter = work.adapter(cell.settings)
        with cell.resources.track_running(identity.id), bodies.observing():
            async with (
                observed.rooms(baseline_ws, old_room, new_room),
                reply_capture(old_room, deadline_s=WORKER_BUDGET.deadline_s) as old,
                reply_capture(new_room, deadline_s=WORKER_BUDGET.deadline_s) as fresh,
                running_agent_with_handle(
                    identity,
                    adapter,
                    cell.settings,
                    session_config=SessionConfig(
                        max_working_state_seconds=None, max_cycle_seconds=None
                    ),
                    link_factory=observed.link,
                ),
                work.releasing_on_exit(),
            ):
                initial = observed.cursor()
                native_initial = bodies.cursor()
                await user_ops.send_message(
                    old_room,
                    work.instruction(Operation.A),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await work.entered(Operation.A)
                old_body = await bodies.admitted(
                    old_room, work.name, since=native_initial
                )
                await observed.detail(old_room, identity.id, detail, since=initial)
                await observed.report(
                    old_room,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=initial,
                )

                stopping = observed.cursor()
                await user_ops.stop_agent(old_room)
                control = await observed.control(
                    old_room, since=stopping, agent_id=identity.id
                )
                assert not work[Operation.A].release.is_set()
                assert not work[Operation.A].settled.is_set()

                new_phase = observed.cursor()
                native_new = bodies.cursor()
                new_message = await user_ops.send_message(
                    new_room,
                    work.instruction(Operation.B),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                await work.entered(Operation.B)
                new_body = await bodies.admitted(new_room, work.name, since=native_new)
                await observed.detail(new_room, identity.id, detail, since=new_phase)
                await observed.report(
                    new_room,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=new_phase,
                )

                work[Operation.A].release.set()
                await work.settled(Operation.A)
                await bodies.settled(old_body)
                await observed.control_finished(control)
                await observed.report(
                    old_room, agent_id=identity.id, working=False, since=stopping
                )
                await observed.stopped(old_room, identity.id, since=stopping)

                survival = observed.cursor()
                await observed.report(
                    new_room,
                    agent_id=identity.id,
                    working=True,
                    activity=detail,
                    since=survival,
                )
                finishing = observed.cursor()
                work[Operation.B].release.set()
                await work.settled(Operation.B)
                await bodies.settled(new_body)
                replies = await fresh.wait_for_reply(new_message, identity.id)
                replies.assert_contains_exact(work[Operation.B].result)
                replies.assert_contains_none([work[Operation.A].result])
                await observed.report(
                    new_room, agent_id=identity.id, working=False, since=finishing
                )
                await observed.stopped(new_room, identity.id, since=finishing)
                assert await old.tool_calls(sender_id=identity.id) == []
                assert await fresh.tool_calls(sender_id=identity.id) == []
                observed.assert_private(
                    work.note,
                    *(invocation.result for invocation in work.operations.values()),
                )
