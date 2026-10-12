"""Codex adapter: emission guards for _emit_token_usage_event."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexTokenUsage
from tests.adapters.codex.kit import ToolSchemaFakeTools
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
)


class TestTokenUsageEmission:
    """Emission guards for _emit_token_usage_event."""

    @pytest.mark.asyncio
    async def test_token_usage_event_skipped_when_total_is_zero(self) -> None:
        """_emit_token_usage_event must not emit before any real usage arrives.

        Prior to the fix, `if not usage:` was always False (dataclass
        instances are truthy) so an empty token_usage event could be emitted
        even before Codex sent any thread/tokenUsage/updated notification.
        """

        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Seed an empty CodexTokenUsage (total_tokens == 0) and call the
        # emit helper directly.  It should short-circuit.
        adapter._token_usage["thread-x"] = CodexTokenUsage()
        await adapter._emit_token_usage_event(
            tools=tools, thread_id="thread-x", room_id="room-1"
        )

        usage_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "token_usage"
        ]
        assert usage_events == []
