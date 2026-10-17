"""Two real custom tools share a resource released by their actual host."""

from __future__ import annotations

import pytest

from band.runtime.custom_tools import get_custom_tool_name
from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ResourceManager,
    running_agent_with_handle,
)
from tests.e2e.baseline.toolkit.tools import ToolSpec
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.example_agents.anthropic.kit import EXAMPLE
from tests.example_agents.anthropic.peer import shared_api

TODO = get_custom_tool_name(EXAMPLE["GetTodoInput"])
USER = get_custom_tool_name(EXAMPLE["GetUserInput"])
PROMPT = (
    f"Use {TODO} and {USER} to answer requests about sample data. "
    "Their current values are only available from the tools. "
    "Report the exact todo title and user name using band_send_message."
)


class TestAdapterCleanup:
    @per_adapter(Adapter.ANTHROPIC, prompt=PROMPT, **EXECUTION_REPORTING)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_shared_resource_closes_after_real_tool_turn_and_host_stop(
        self,
        cell: AdapterCell,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        async with shared_api() as (client, base_url, values):
            tools = [
                ToolSpec(model, handler)
                for model, handler in EXAMPLE["shared_tools"](client, base_url)
            ]
            adapter = cell.build(tools=tools)
            adapter.add_cleanup(client.aclose)
            identity = await cell.provision()
            room_id = await resource_manager.provision_room(
                title="e2e-adapter-cleanup", participants=[identity.id]
            )
            with cell.resources.track_running(identity.id):
                async with running_agent_with_handle(
                    identity, adapter, cell.settings
                ) as host:
                    async with reply_capture(room_id) as capture:
                        mid = await user_ops.send_message(
                            room_id,
                            "Read todo 1 and user 1; report the todo title and user name exactly.",
                            mention_id=identity.id,
                            mention_name=identity.name,
                        )
                        replies = await capture.wait_for_reply(mid, identity.id)
                        calls = await capture.tool_calls(sender_id=identity.id)
                    calls.assert_fired(TODO, with_args={"todo_id": 1})
                    calls.assert_fired(USER, with_args={"user_id": 1})
                    replies.assert_contains_any([str(values["todo"]["title"])])
                    replies.assert_contains_any([str(values["user"]["name"])])
                    assert not client.is_closed
                    await host.stop()
                    assert client.is_closed
