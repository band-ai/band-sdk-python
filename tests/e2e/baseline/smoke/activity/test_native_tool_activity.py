"""A held native shell command shows only its tool name, then clears with a stop."""

from __future__ import annotations

import pytest

from band.core.types import AdapterFeatures
from band.runtime.activity import TOOL_CALL, UNKNOWN_ACTIVITY_TOOL_NAME
from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.agents import Adapter, not_applicable, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.behavior.approvals.kit import approval_room
from tests.e2e.baseline.smoke.samples.approvals import DIALECTS
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activity import ActivityCapture
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.resultexchange import ExchangeOperation, ResultExchange
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=6)
NO_NATIVE_DIALECT = "no E2E native-command dialect"
# The name each dialect's adapter reports for its native shell tool; ACP
# clients do not name their tools.
SHELL_TOOL_NAMES = {
    Adapter.CLAUDE_SDK: "Bash",
    Adapter.CODEX: "exec",
    Adapter.CURSOR_ACP: UNKNOWN_ACTIVITY_TOOL_NAME,
    Adapter.OPENCODE: "bash",
}
REQUEST = (
    "Run this command exactly once with your native shell tool (Bash where "
    "available). Do not inspect, poll, or replace it. Then send its exact "
    "complete output to this room:\n"
)


class TestNativeToolActivity:
    @per_adapter(exclude=not_applicable(DIALECTS, NO_NATIVE_DIALECT))
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_held_native_command_reports_its_name_and_clears(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        baseline_ws: TrackingWebSocketClient,
        result_exchange: ResultExchange,
    ) -> None:
        observed = ActivityCapture(deadline_s=BUDGET.deadline_s)
        shell = {"kind": TOOL_CALL, "name": SHELL_TOOL_NAMES[cell.adapter_id]}
        async with (
            approval_room(
                cell,
                user_ops,
                reply_capture,
                label="native",
                budget=BUDGET,
                session_config=SessionConfig(
                    max_working_state_seconds=None, max_cycle_seconds=None
                ),
                link_factory=observed.link,
                features=AdapterFeatures(emit=frozenset()),
                approval="auto_accept",
            ) as (room, _),
            observed.rooms(baseline_ws, room.room_id),
            result_exchange.releasing_on_exit(),
        ):
            agent = room.agent
            since = observed.cursor()
            _, request = await room.post(
                REQUEST + result_exchange.command(ExchangeOperation.PRODUCE)
            )
            await result_exchange.wait(
                result_exchange.producer.entered, phase="native producer entered"
            )
            await observed.held(
                room.room_id, agent.id, shell, since=since, entered=observed.cursor()
            )

            finishing = observed.cursor()
            result_exchange.producer.release.set()
            await result_exchange.wait(
                result_exchange.producer.settled, phase="native producer settled"
            )
            result = result_exchange.result.decode("ascii")
            reply = await room.capture.wait_for_reply(request, agent.id)
            reply.assert_contains_exact(result)
            await observed.finished(room.room_id, agent.id, since=finishing)
            observed.assert_private(result_exchange.note, result)
