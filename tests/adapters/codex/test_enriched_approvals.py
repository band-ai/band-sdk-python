"""Codex adapter: enriched approvals."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexSessionState
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
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


class TestEnrichedApprovals:
    async def test_approve_session_accepts_for_the_session_and_remembers_it(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            event_request(
                10, "item/commandExecution/requestApproval", {"command": "npm test"}
            ),
            turn_completed(),
        )

        await room.send("run tests")
        await room.send("/approve-session req-10")
        await room.settled()

        assert room.client.responses == [(10, {"decision": "acceptForSession"})]
        assert "commandExecution:npm test" in room.adapter._session_approved[ROOM_ID]
        assert any("session-level" in message for message in room.chat)

    @pytest.mark.asyncio
    async def test_approval_audit_trail_emitted(self) -> None:
        """Approval decisions emit audit trail task events."""
        events = [
            event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(approval_mode="auto_decline")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        audit_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "approval_resolution"
        ]
        assert len(audit_events) == 1
        assert audit_events[0]["metadata"]["codex_approval_decision"] == "decline"
        assert audit_events[0]["metadata"]["codex_decided_by"] == "policy:auto_decline"

    @pytest.mark.asyncio
    async def test_sandbox_command_changes_mode(self) -> None:
        """The /sandbox command sets a per-room override, not mutating global config."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/sandbox read-only"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Per-room override is set, global config is unchanged
        assert adapter._sandbox_overrides.get("room-1") == "read-only"
        assert adapter.config.sandbox is None
        assert "read-only" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_sandbox_command_is_per_room(self) -> None:
        """Sandbox override in one room does not affect other rooms."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Agent", "A coding agent")

        adapter._sandbox_overrides["room-1"] = "read-only"

        assert adapter._effective_sandbox("room-1") == "read-only"
        assert adapter._effective_sandbox("room-2") is None

    @pytest.mark.asyncio
    async def test_sandbox_danger_full_access_requires_confirm_flag(self) -> None:
        """Escalating to danger-full-access without --confirm shows a prompt."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/sandbox danger-full-access"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Override should NOT be set — confirmation was required
        assert "room-1" not in adapter._sandbox_overrides
        assert "--confirm" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_sandbox_escalation_to_danger_full_access_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Escalating to danger-full-access with --confirm logs a warning."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        with caplog.at_level(logging.WARNING, logger="band.adapters.codex"):
            await deliver_message(
                adapter,
                make_platform_message(content="/sandbox danger-full-access --confirm"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert adapter._sandbox_overrides.get("room-1") == "danger-full-access"
        assert any(
            "Sandbox escalated to danger-full-access" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_sandbox_command_rejects_invalid_mode(self) -> None:
        """The /sandbox command rejects invalid modes."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/sandbox invalid-mode"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert adapter.config.sandbox is None
        assert "room-1" not in adapter._sandbox_overrides
        assert "Invalid sandbox mode" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_permissions_command_shows_state(self) -> None:
        """/permissions shows current effective permissions."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(content="/permissions"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert "Effective permissions:" in tools.messages_sent[0]["content"]
        assert "approval_mode: manual" in tools.messages_sent[0]["content"]
