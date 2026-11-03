"""Live OpenCode recovery after the in-process Band MCP server crashes.

Unit tests fake ``opencode serve`` and assume same-name re-registration replaces
the old SSE connection. These smokes pin that contract against a real serve: a
structural (no LLM) re-register, then a conversational turn that must still
deliver a Band reply after the crash.

Run with:
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_opencode_mcp_crash.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from band.integrations.mcp import BandMCPTransport
from band.runtime.tools import BandTool
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
from tests.mcpclient import crash_backend, served_tool_names

# Turn-1 reply, turn-1 processed, turn-2 reply.
BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=3)


class TestOpencodeMcpCrash:
    @per_adapter(Adapter.OPENCODE, prompt=LIVENESS_REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_crashed_server_is_re_registered_with_live_serve(
        self, cell: AdapterCell
    ) -> None:
        """``opencode serve`` accepts a same-name re-register on the new URL.

        No model turn: ``_ensure_client_started`` registers Band MCP before and
        after the crash so this cell pins serve behaviour, not LLM behaviour.
        """
        identity = await cell.provision(label="mcp-crash-opencode-struct")
        adapter = cell.build()
        with cell.resources.track_running(identity.id):
            async with running_agent(identity, adapter, cell.settings):
                await adapter._ensure_client_started()
                before = adapter._registration
                assert before is not None, "OpenCode never registered Band MCP"
                mcp_name = adapter._mcp_server_name

                crashed = await crash_backend(adapter._mcp)
                assert not crashed.is_running

                await adapter._ensure_client_started()
                after = adapter._registration
                assert after is not None, (
                    "OpenCode did not re-register Band MCP after the crash"
                )
                assert adapter._mcp_server_name == mcp_name
                assert after.url != before.url, (
                    "replacement kept the dead registration URL"
                )
                served = await served_tool_names(
                    after.url, BandMCPTransport.SSE, headers=after.headers
                )
                assert BandTool.SEND_MESSAGE in served

    @per_adapter(Adapter.OPENCODE, prompt=LIVENESS_REPLY_PROMPT)
    @flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_reply_survives_a_crashed_band_mcp_server(
        self,
        cell: AdapterCell,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        """A second Band reply after a crash still lands; registration replaced."""
        identity = await cell.provision(label="mcp-crash-opencode-live")
        adapter = cell.build()
        room_id = await resource_manager.provision_room(
            title="e2e-opencode-mcp-crash", participants=[identity.id]
        )
        before_marker, after_marker = unique_marker("before"), unique_marker("after")

        with cell.resources.track_running(identity.id):
            async with (
                running_agent(identity, adapter, cell.settings),
                reply_capture(room_id) as capture,
            ):
                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(before_marker),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, since=mark, deadline_s=BUDGET.deadline_s
                )
                replies.assert_contains_any([before_marker])
                await capture.wait_for_processed(
                    mid, identity.id, deadline_s=BUDGET.deadline_s
                )

                prior = adapter._registration
                assert prior is not None
                mcp_name = adapter._mcp_server_name
                await crash_backend(adapter._mcp)

                mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    liveness_probe(after_marker),
                    mention_id=identity.id,
                    mention_name=identity.name,
                )
                replies = await capture.wait_for_reply(
                    mid, identity.id, since=mark, deadline_s=BUDGET.deadline_s
                )
                replies.assert_contains_any([after_marker])

                live = adapter._registration
                assert live is not None
                assert adapter._mcp_server_name == mcp_name
                assert live.url != prior.url, (
                    "reply arrived without OpenCode re-registering the new URL"
                )
