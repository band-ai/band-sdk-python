"""An adapter without observable tool activity reports reasoning for its turn."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures
from band.runtime.activity import REASONING
from band.runtime.tools import BandTool
from tests.e2e.baseline.agents import Adapter, not_applicable, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    COST_AGENT_SYSTEM_PROMPT,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
THINKING = REASONING.model_dump(exclude_none=True)
REASONING_ONLY = (Adapter.LETTA, Adapter.CREWAI_FLOW)
REPORTS_TOOLS = "reports observable tool activity"
QUESTIONS = (
    "Which planet is closest to the sun? Answer in one short sentence.",
    "How many days are in a week? Answer in one short sentence.",
)


class TestReasoningOnlyActivity:
    @per_adapter(exclude=not_applicable(REASONING_ONLY, REPORTS_TOOLS))
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_second_turn_reports_reasoning_and_clears(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
    ) -> None:
        identity = await cell.provision()
        room = await cell.resources.provision_room(
            title="e2e-activity-reasoning", participants=[identity.id]
        )
        observed = ActivityCapture(deadline_s=BUDGET.deadline_s)
        # Sent with the question; a leak into an activity frame would carry it.
        marker = unique_marker("reason")
        first, second = QUESTIONS
        async with (
            observed.rooms(baseline_ws, room),
            reply_capture(room, deadline_s=BUDGET.deadline_s) as capture,
            cell.run_as_with_handle(
                identity,
                prompt=COST_AGENT_SYSTEM_PROMPT,
                features=AdapterFeatures(emit=frozenset()),
                link_factory=observed.link,
            ),
        ):
            sent = await user_ops.send_message(
                room, first, mention_id=identity.id, mention_name=identity.name
            )
            await capture.wait_for_reply(sent, identity.id)

            # Only a process's first report can lack detail, so turn two is
            # order-independent.
            since, replies = observed.cursor(), capture.messages.snapshot()
            sent = await user_ops.send_message(
                room,
                f"{second} ({marker})",
                mention_id=identity.id,
                mention_name=identity.name,
            )
            thinking = await observed.report(
                room,
                agent_id=identity.id,
                working=True,
                activity=THINKING,
                since=since,
            )
            await capture.wait_for_reply(sent, identity.id, since=replies)
            await observed.finished(room, identity.id, since=thinking.sequence)
            observed.assert_private(marker, tool_names=frozenset(BandTool))
