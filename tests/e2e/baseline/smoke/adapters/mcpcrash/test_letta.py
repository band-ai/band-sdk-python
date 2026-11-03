"""Live Letta: self-hosted Band MCP crash → repoint, then a Band send still works."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.smoke.adapters.mcpcrash.kit import (
    BUDGET,
    LETTA_TIMEOUT_EXTRA,
    LettaRegistration,
    crash_band_mcp,
    ensure_letta_mcp,
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


class TestLettaMcpCrash:
    @per_adapter(Adapter.LETTA)
    @pytest.mark.timeout(extra=LETTA_TIMEOUT_EXTRA)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_crashed_server_is_repointed_for_live_letta(
        self, cell: AdapterCell
    ) -> None:
        """Letta keeps the same registration and rediscovers tools after a crash."""
        async with hosted_adapter(cell, label="mcp-crash-letta-struct") as host:
            assert host.adapter.config.mcp.mode == "self_host", (
                "this smoke proves the self-hosted MCP path; unset MCP_SERVER_URL "
                "(it switches the builder to an external band-mcp)"
            )
            before = LettaRegistration.capture(host.adapter)
            await crash_band_mcp(host.adapter)
            await ensure_letta_mcp(host.adapter)
            await before.assert_repointed(host.adapter)

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
        before_marker, after_marker = unique_marker("before"), unique_marker("after")
        async with hosted_room(
            cell,
            resource_manager,
            user_ops,
            reply_capture,
            label="mcp-crash-letta-live",
            title="e2e-letta-mcp-crash",
            config={"auto_relay": False},
            budget=BUDGET,
        ) as room:
            before = LettaRegistration.capture(room.adapter)
            first = await room.liveness_reply(before_marker)
            first.replies.assert_contains_any([before_marker])

            await crash_band_mcp(room.adapter)

            second = await room.liveness_reply(after_marker)
            second.replies.assert_contains_any([after_marker])
            before.assert_stable(room.adapter)
