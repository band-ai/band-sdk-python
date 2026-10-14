"""Codex adapter: accept for session."""

from __future__ import annotations

from collections import OrderedDict

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexApprovalMethod
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)


class TestAcceptForSession:
    @pytest.mark.asyncio
    async def test_session_auto_approval_sends_accept_for_session(self) -> None:
        """Session auto-approved requests send 'acceptForSession' to Codex."""
        events = [
            event_request(
                20,
                CodexApprovalMethod.COMMAND_EXECUTION,
                {"command": "npm install"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(approval_mode="manual")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed session approval for the exact command
        adapter._session_approved[ROOM_ID] = OrderedDict(
            [("commandExecution:npm install", None)]
        )

        await send_bootstrap(adapter, content="install deps", tools=tools)

        # Decision should be acceptForSession
        responses = fake_client.responses
        decisions = [r.get("decision") for _, r in responses]
        assert "acceptForSession" in decisions
