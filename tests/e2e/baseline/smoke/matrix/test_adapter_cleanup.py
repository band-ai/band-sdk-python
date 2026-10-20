"""Live host-owned resources survive real tool work and fresh replacement lifetimes."""

from __future__ import annotations

from datetime import datetime

import pytest

from band.agent import Agent
from band.runtime.oneshot import OneShotInvoker
from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.matrix.cleanup import (
    BUDGET,
    HOST_TYPES,
    PROMPT,
    TODO,
    USER,
    resource_tools,
    running_cleanup_host,
    stop_cleanup_host,
)
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.example_agents.anthropic.peer import shared_api


class TestAdapterCleanup:
    @per_adapter(runs_tool_loop=True, prompt=PROMPT, **EXECUTION_REPORTING)
    @pytest.mark.parametrize("host_type", HOST_TYPES, ids=lambda host: host.__name__)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_shared_resource_closes_after_real_tool_turn_and_host_stop(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        host_type: type[Agent | OneShotInvoker],
    ) -> None:
        identity = await cell.provision()
        room_id = await cell.resources.provision_room(
            title=f"e2e-adapter-cleanup-{identity.id}", participants=[identity.id]
        )
        previous_turn: datetime | None = None
        for _ in range(2):
            async with shared_api() as (client, base_url, values):
                adapter = cell.build(tools=resource_tools(client, base_url))
                adapter.add_cleanup(client.aclose)
                async with running_cleanup_host(
                    cell, identity, adapter, room_id, host_type
                ) as host:
                    async with reply_capture(room_id) as capture:
                        mid = await user_ops.send_message(
                            room_id,
                            "Read current todo 1 and user 1 using both tools again; "
                            "report the todo title and user name exactly.",
                            mention_id=identity.id,
                            mention_name=identity.name,
                        )
                        replies = await capture.wait_for_reply(
                            mid, identity.id, deadline_s=BUDGET.deadline_s
                        )
                        calls = await capture.tool_calls(
                            sender_id=identity.id, since=previous_turn
                        )
                        previous_turn = capture.turn_boundary()
                    calls.assert_fired(TODO, with_args={"todo_id": 1})
                    calls.assert_fired(USER, with_args={"user_id": 1})
                    replies.assert_contains_any([str(values["todo"]["title"])])
                    replies.assert_contains_any([str(values["user"]["name"])])
                    assert not client.is_closed
                    await stop_cleanup_host(host)
                    assert client.is_closed
