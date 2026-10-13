"""Codex adapter: adapter must survive notifications that are missing or misshapen."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import CodexSessionState
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestMalformedPayloadTolerance:
    """Adapter must survive notifications that are missing or misshapen."""

    @pytest.mark.asyncio
    async def test_error_event_with_non_dict_error_field(self) -> None:
        """`error` notification where `error` is a string must not crash the turn."""
        events = [
            event_notification("error", {"error": "oops"}),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
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

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["message"] == "oops"

    @pytest.mark.asyncio
    async def test_failed_turn_after_error_notification_reports_once(self) -> None:
        """An `error` notification followed by a `turn/completed` with
        status=failed for the same incident must report only one failure."""
        events = [
            event_notification("error", {"error": {"message": "boom"}}),
            event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "failed",
                        "items": [],
                        "error": {"message": "boom"},
                    }
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        with pytest.raises(TurnResultAlreadyReported):
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

        assert len(reported_failures(tools)) == 1

    @pytest.mark.asyncio
    async def test_failed_turn_with_falsy_scalar_error_uses_clean_fallback(
        self,
    ) -> None:
        """A falsy, non-dict `error` (e.g. ``False``) must not become the
        literal string "False" in the reported failure message."""
        events = [
            event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "failed",
                        "items": [],
                        "error": False,
                    }
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        with pytest.raises(TurnResultAlreadyReported):
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

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["message"] == "Codex error: unknown"

    @pytest.mark.asyncio
    async def test_turn_completed_without_items_key(self) -> None:
        """turn/completed missing `items` is treated as an empty turn, not a crash."""
        events = [
            event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "completed"}},
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
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

    @pytest.mark.asyncio
    async def test_turn_plan_updated_with_garbage_steps(self) -> None:
        """Plan deltas containing non-list `steps` must be skipped, not crash."""
        events = [
            event_notification(
                "turn/plan/updated",
                {"plan": {"steps": "not-a-list"}},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_plan_events=True)
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
