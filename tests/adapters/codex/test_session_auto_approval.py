"""Codex adapter: session auto approval."""

from __future__ import annotations

from collections import OrderedDict

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexSessionState
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestSessionAutoApproval:
    @pytest.mark.asyncio
    async def test_session_auto_approves_matching_command_binary(self) -> None:
        """After session-level approval for npm, a new npm command is auto-approved."""
        # Two command execution requests in one turn — first will be manually
        # approved, second should be auto-approved by session policy.
        events = [
            event_request(
                20,
                "item/commandExecution/requestApproval",
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

        # Pre-seed the session-approved set as if /approve-session was used for "npm install"
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm install", None)]
        )

        await deliver_message(
            adapter,
            make_platform_message(content="install deps"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The request should have been auto-approved via session policy
        responses = fake_client.responses
        assert any(
            result.get("decision") in {"accept", "acceptForSession"}
            for _, result in responses
        )

    @pytest.mark.asyncio
    async def test_session_does_not_auto_approve_different_command(self) -> None:
        """Session approval for 'npm install' does NOT auto-approve 'npm publish'."""
        events = [
            event_request(
                30,
                "item/commandExecution/requestApproval",
                {"command": "npm publish"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(approval_mode="auto_decline")
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed session approval for "npm install" only
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm install", None)]
        )

        await deliver_message(
            adapter,
            make_platform_message(content="publish package"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # npm publish should have been declined, not auto-approved
        responses = fake_client.responses
        assert any(result.get("decision") == "decline" for _, result in responses)

    @pytest.mark.asyncio
    async def test_session_binary_granularity_approves_same_binary(self) -> None:
        """With binary granularity, session approval for 'npm test' auto-approves 'npm install'."""
        events = [
            event_request(
                40,
                "item/commandExecution/requestApproval",
                {"command": "npm install"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                approval_mode="manual", session_approval_granularity="binary"
            ),
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed session approval for npm binary
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm", None)]
        )

        await deliver_message(
            adapter,
            make_platform_message(content="install deps"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # npm install should be auto-approved because binary matches
        responses = fake_client.responses
        assert any(
            result.get("decision") in {"accept", "acceptForSession"}
            for _, result in responses
        )
