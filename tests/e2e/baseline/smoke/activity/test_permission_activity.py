"""A pending room approval shows as permission activity, then clears once approved."""

from __future__ import annotations

import pytest

from band.runtime.activity import PERMISSION
from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import (
    Adapter,
    ExcludedAdapter,
    not_applicable,
    per_adapter,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.behavior.approvals.kit import (
    approval_room,
    readback_commands,
)
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    Outcome,
    command_request,
    written_lines,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    unique_marker,
    usage_features,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
WAITING = PERMISSION.model_dump(exclude_none=True)
NOT_HELD = (
    *not_applicable(DIALECTS, "no E2E room-approval dialect"),
    ExcludedAdapter(
        Adapter.CURSOR_ACP,
        "Cursor releases the turn while a decision is outstanding, so nothing is held",
    ),
)


class TestPermissionActivity:
    @per_adapter(exclude=NOT_HELD)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_pending_approval_reports_permission_and_clears(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
    ) -> None:
        observed = ActivityCapture(deadline_s=BUDGET.deadline_s)
        marker = unique_marker("permission")
        done = unique_marker("closed")
        async with approval_room(
            cell,
            user_ops,
            reply_capture,
            label="permission",
            budget=BUDGET,
            session_config=SessionConfig(
                max_working_state_seconds=None, max_cycle_seconds=None
            ),
            link_factory=observed.link,
            # Usage events close the turn; narration stays off.
            features=usage_features(),
        ) as (room, workdir):
            agent, target = room.agent, workdir / "permission.txt"
            async with observed.rooms(baseline_ws, room.room_id):
                since = observed.cursor()
                await room.say(command_request(marker, target, done=done))
                [request] = await room.requests(1)
                await observed.held(
                    room.room_id,
                    agent.id,
                    WAITING,
                    since=since,
                    entered=observed.cursor(),
                )

                after_request = room.capture.messages.snapshot()
                finishing = observed.cursor()
                await room.decide(Outcome.APPROVE, request)
                await room.closed(
                    room.dialect.notice(Outcome.APPROVE, request),
                    since=after_request,
                    closing_reply=done,
                    allowed_followup_commands=readback_commands(target),
                )
                assert written_lines(target) == [marker]
                await observed.finished(room.room_id, agent.id, since=finishing)
                observed.assert_private(marker, done)
