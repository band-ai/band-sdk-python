"""Codex adapter: an approval is still answered when the room cannot be told about it."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.testing import (
    reported_failures,
)
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


class TestApprovalNotificationFailures:
    @pytest.mark.asyncio
    async def test_approval_request_auto_decline(self) -> None:
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

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"
        assert len(tools.messages_sent) == 1
        assert "Approval requested" in tools.messages_sent[0]["content"]
        assert "rm -rf tmp" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_auto_approval_responds_even_if_notification_fails(self) -> None:
        class FailingNotifyTools(ToolSchemaFakeTools):
            async def send_notice(
                self, content: str, mentions: list[dict[str, str]] | None = None
            ) -> Any:
                raise RuntimeError("notification failed")

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
            fake_client,
            config=CodexAdapterConfig(
                approval_mode="auto_decline", approval_text_notifications=True
            ),
        )
        tools = FailingNotifyTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"

    @pytest.mark.asyncio
    async def test_manual_approval_responds_with_decline_if_notification_fails(
        self,
    ) -> None:
        class FailingNotifyTools(ToolSchemaFakeTools):
            async def send_notice(
                self, content: str, mentions: list[dict[str, str]] | None = None
            ) -> Any:
                raise RuntimeError("notification failed")

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
            fake_client, config=CodexAdapterConfig(approval_mode="manual")
        )
        tools = FailingNotifyTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"
        assert ROOM_ID not in adapter._pending_approvals

        # The Band-delivery hiccup that caused this auto-decline must itself
        # be reported -- otherwise it's indistinguishable from a genuine
        # human decision, with no signal at all that anything went wrong.
        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "codex"

        # The human sender was never actually notified, so the audit trail
        # must not credit/blame them for this decision -- it was forced by
        # the delivery failure, same as every other forced-decline path.
        audit_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "approval_resolution"
        ]
        assert len(audit_events) == 1
        assert audit_events[0]["metadata"]["codex_decided_by"] == "system_fallback"
