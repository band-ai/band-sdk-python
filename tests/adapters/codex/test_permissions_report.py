"""Codex adapter: /permissions reports the room's capped approval state."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexApprovalMethod
from tests.adapters.codex.kit import ROOM_ID, ToolSchemaFakeTools, send_bootstrap
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)


class TestPermissionsReport:
    """/permissions lists what the configured caps keep of a room's approvals."""

    @pytest.mark.asyncio
    async def test_the_report_honors_the_configured_caps(self) -> None:
        commands = ("ls", "pwd", "whoami")
        client = FakeCodexClient(
            events=[
                *(
                    event_request(
                        i, CodexApprovalMethod.COMMAND_EXECUTION, {"command": command}
                    )
                    for i, command in enumerate(commands)
                ),
                turn_completed(),
            ]
        )
        adapter = make_codex_adapter(
            client,
            config=CodexAdapterConfig(
                approval_mode="auto_decline",
                max_approval_audit_per_room=2,
                max_session_approved_per_room=1,
            ),
        )
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="look around")
        for command in commands:
            adapter._approvals.approve_for_session(
                ROOM_ID, f"commandExecution:{command}"
            )

        tools = ToolSchemaFakeTools()
        await send_bootstrap(adapter, content="/permissions", tools=tools)
        report = tools.messages_sent[-1]["content"]

        assert "- approval_history: 2 decisions" in report
        assert "- session_approved patterns: 1\n  - commandExecution:whoami" in report
