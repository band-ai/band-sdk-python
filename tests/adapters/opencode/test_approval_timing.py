"""Human deliberation is excluded from the OpenCode compute budget."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.dispatch import deliver_message
from tests.adapters.hold import Hold
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    ObservedOpencodeAdapter,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)
from tests.adapters.waiting import spin
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

COMPUTE_BUDGET_S = 8.0


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestApprovalTiming:
    @pytest.mark.parametrize(
        ("deliberation", "computation"),
        [(16.0, 6.0), (6.0, 6.0)],
        ids=[
            "manual-approval-pauses-timeout",
            "early-approval-preserves-resumed-budget",
        ],
    )
    async def test_human_time_is_excluded_from_the_compute_budget(
        self,
        make_adapter: Callable[..., OpencodeAdapter],
        tools: FakeAgentTools,
        deliberation: float,
        computation: float,
    ) -> None:
        finishing = Hold()
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_permission("sess-1", "req-1"),
                ]
            ],
            reply_permission_events={
                "req-1": [
                    finishing.wait,
                    event_text_part("sess-1", "msg-1", "Approved and done."),
                    event_session_idle("sess-1"),
                ]
            },
        )
        async with controlled_clock() as clock:
            adapter = cast(
                ObservedOpencodeAdapter,
                make_adapter(
                    client,
                    adapter_type=ObservedOpencodeAdapter,
                    config=OpencodeAdapterConfig(
                        approval_mode="manual",
                        turn_timeout_s=COMPUTE_BUDGET_S,
                        approval_wait_timeout_s=64.0,
                    ),
                ),
            )
            await run_single_turn(adapter, tools, content="run it")
            await adapter.watcher_started.wait()
            watcher = adapter._rooms["room-1"].turn_task
            assert watcher is not None
            clock.advance(deliberation)
            await spin()
            assert client.aborted_sessions == []
            await deliver_message(
                adapter,
                make_platform_message(content="approve req-1"),
                tools_protocol(FakeAgentTools()),
                OpencodeSessionState(session_id="sess-1", room_id="room-1"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )
            try:
                await finishing.arrived()
                clock.advance(computation)
                await spin()
                assert client.aborted_sessions == []
                finishing.released.set()
                await watcher
                assert tools.turn.complete
                assert tools.chat[-1] == "Approved and done."
                assert failure_reports(tools) == []
            finally:
                finishing.released.set()
