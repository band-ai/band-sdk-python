"""A Slack wrapper owns resources used by real Band turns in its inner adapter."""

from __future__ import annotations

import pytest

from band.agent import Agent
from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.types import SlackApp
from band.runtime.oneshot import OneShotInvoker
from tests.e2e.baseline.agents import Adapter, per_adapter
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


class TestSlackCleanup:
    @per_adapter(Adapter.ANTHROPIC, prompt=PROMPT, **EXECUTION_REPORTING)
    @pytest.mark.parametrize("host_type", HOST_TYPES, ids=lambda host: host.__name__)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_outer_registration_closes_inner_tool_resource(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        host_type: type[Agent | OneShotInvoker],
    ) -> None:
        identity = await cell.provision()
        room_id = await cell.resources.provision_room(
            title=f"e2e-slack-resource-owner-{identity.id}", participants=[identity.id]
        )
        async with shared_api() as (client, base_url, values):
            wrapper = SlackAdapter(
                SlackAdapterConfig(
                    apps=(
                        SlackApp(
                            slug="local", bot_token="local", signing_secret="local"
                        ),
                    ),
                    transport="http",
                ),
                inner=cell.build(tools=resource_tools(client, base_url)),
            )
            wrapper.add_cleanup(client.aclose)
            async with running_cleanup_host(
                cell, identity, wrapper, room_id, host_type
            ) as host:
                async with reply_capture(room_id) as capture:
                    mid = await user_ops.send_message(
                        room_id,
                        "Read current todo 1 and user 1; report both exact values.",
                        mention_id=identity.id,
                        mention_name=identity.name,
                    )
                    replies = await capture.wait_for_reply(
                        mid, identity.id, deadline_s=BUDGET.deadline_s
                    )
                    calls = await capture.tool_calls(sender_id=identity.id)
                calls.assert_fired(TODO, with_args={"todo_id": 1})
                calls.assert_fired(USER, with_args={"user_id": 1})
                replies.assert_contains_any([str(values["todo"]["title"])])
                replies.assert_contains_any([str(values["user"]["name"])])
                assert not client.is_closed
                await stop_cleanup_host(host)
                assert client.is_closed
