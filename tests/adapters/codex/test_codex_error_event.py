"""Codex adapter: a Codex error notification reaches the room."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex.types import (
    CodexSessionState,
)
from band.testing import (
    events_of_type,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
)
from tests.adapters.dispatch import deliver_message


class TestCodexErrorEvent:
    @pytest.mark.asyncio
    async def test_codex_error_emits_event_unconditionally(self) -> None:
        """Non-retryable Codex errors always emit a structured error event."""
        fake_client = FakeCodexClient(
            events=[
                event_notification(
                    "error",
                    {"error": {"message": "Something went wrong"}, "willRetry": False},
                ),
                event_notification(
                    "turn/completed",
                    {"turn": {"id": "turn-1", "status": "failed"}},
                ),
            ],
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id=ROOM_ID, content="do something")
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_message(
                adapter,
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id=ROOM_ID,
            )

        error_events = events_of_type(tools, "error")
        assert len(error_events) == 1
        assert "Something went wrong" in error_events[0]["content"]
