"""Live OpenCode: Band MCP crash → same-name re-register, then a Band reply still works."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.smoke.adapters.mcpcrash.kit import (
    BUDGET,
    OpencodeRegistration,
    crash_band_mcp,
    ensure_opencode_mcp,
    hosted_adapter,
    hosted_room,
)
from tests.e2e.baseline.smoke.samples.sample_agents import (
    LIVENESS_REPLY_PROMPT,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestOpencodeMcpCrash:
    @per_adapter(Adapter.OPENCODE)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_crashed_server_is_re_registered_with_live_serve(
        self, cell: AdapterCell
    ) -> None:
        """``opencode serve`` accepts a same-name re-register on the new URL."""
        async with hosted_adapter(cell, label="mcp-crash-opencode-struct") as host:
            await ensure_opencode_mcp(host.adapter)
            before = OpencodeRegistration.capture(host.adapter)
            await crash_band_mcp(host.adapter)
            await ensure_opencode_mcp(host.adapter)
            await before.assert_reregistered(host.adapter)

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
        before_marker, after_marker = unique_marker("before"), unique_marker("after")
        async with hosted_room(
            cell,
            resource_manager,
            user_ops,
            reply_capture,
            label="mcp-crash-opencode-live",
            title="e2e-opencode-mcp-crash",
            # Relay would keep the room green with a dead MCP tool path.
            config={"fallback_send_agent_text": False},
            budget=BUDGET,
        ) as room:
            first = await room.liveness_reply(before_marker)
            first.replies.assert_contains_any([before_marker])
            before = OpencodeRegistration.capture(room.adapter)

            await crash_band_mcp(room.adapter)

            second = await room.liveness_reply(after_marker)
            second.replies.assert_contains_any([after_marker])
            before.assert_replaced(room.adapter)
