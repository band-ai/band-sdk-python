"""Live Letta recovery after the self-hosted Band MCP server crashes.

Unit tests fake the Letta API and assume an ``mcp_servers.update`` repoint
takes effect without re-attaching tools. These smokes pin that contract against
a real self-hosted Letta server: a structural (no LLM) repoint, then a
conversational turn that must travel through ``band_send_message`` with
auto-relay disabled.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=letta uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_letta_mcp_crash.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    LIVENESS_REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.mcpclient import crash_backend

# Turn-1 reply, turn-1 processed, turn-2 reply.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=3)
# Letta MCP sync stalls are 60s each; keep at least the showcase floor.
LETTA_TIMEOUT_EXTRA = max(BUDGET.extra_s, 120)


class TestLettaMcpCrash:
    @per_adapter(Adapter.LETTA)
    @pytest.mark.timeout(extra=LETTA_TIMEOUT_EXTRA)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_crashed_server_is_repointed_for_live_letta(
        self, cell: AdapterCell
    ) -> None:
        """Letta keeps the same registration and rediscovers tools after a crash.

        No model turn: recovery is driven through ``ensure_ready`` so this cell
        pins the external server's URL update, not LLM behaviour. Ephemeral
        ports may reuse a number, so port inequality is not required.
        """
        identity = await cell.provision(label="mcp-crash-letta-struct")
        adapter = cell.build()
        assert adapter.config.mcp.mode == "self_host", (
            "this smoke proves the self-hosted MCP path; unset MCP_SERVER_URL "
            "(it switches the builder to an external band-mcp)"
        )
        with cell.resources.track_running(identity.id):
            async with running_agent(identity, adapter, cell.settings):
                server_id = adapter._mcp.server_id
                tool_ids = list(adapter._mcp.tool_ids)
                assert server_id, "adapter registered no MCP server with Letta"
                assert tool_ids, (
                    "Letta discovered no tools from the self-hosted MCP server"
                )

                crashed = await crash_backend(adapter._mcp.backend)
                assert not crashed.is_running

                await adapter._mcp.ensure_ready(adapter._client)

                assert adapter._mcp.ready
                assert adapter._mcp.server_id == server_id
                assert adapter._mcp.tool_ids == tool_ids
                discovered = await adapter._client.mcp_servers.tools.list(
                    mcp_server_id=server_id
                )
                assert [tool.id for tool in discovered if getattr(tool, "id", None)] == (
                    tool_ids
                ), (
                    "Letta could not rediscover Band tools at the repointed URL"
                )

    @per_adapter(Adapter.LETTA, prompt=LIVENESS_REPLY_PROMPT)
    @flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
    @pytest.mark.timeout(extra=LETTA_TIMEOUT_EXTRA)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_reply_survives_a_crashed_band_mcp_server(
        self,
        cell: AdapterCell,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        """A second Band send after a crash still arrives with relay disabled."""
        identity = await cell.provision(label="mcp-crash-letta-live")
        adapter = cell.build()
        adapter.config = adapter.config.model_copy(update={"auto_relay": False})
        room_id = await resource_manager.provision_room(
            title="e2e-letta-mcp-crash", participants=[identity.id]
        )
        before, after = unique_marker("before"), unique_marker("after")

        with cell.resources.track_running(identity.id):
            async with (
                running_agent(identity, adapter, cell.settings),
                reply_capture(room_id) as capture,
            ):
                server_id = adapter._mcp.server_id
                tool_ids = list(adapter._mcp.tool_ids)
                assert server_id and tool_ids

                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(before),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, since=mark, deadline_s=BUDGET.deadline_s
                )
                replies.assert_contains_any([before])
                await capture.wait_for_processed(
                    mid, identity.id, deadline_s=BUDGET.deadline_s
                )

                await crash_backend(adapter._mcp.backend)

                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(after),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, since=mark, deadline_s=BUDGET.deadline_s
                )
                replies.assert_contains_any([after])

                assert adapter._mcp.server_id == server_id
                assert adapter._mcp.tool_ids == tool_ids
                assert adapter._mcp.ready
