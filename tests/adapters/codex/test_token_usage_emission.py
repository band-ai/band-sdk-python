"""Codex adapter: token usage emission."""

from __future__ import annotations

import pytest

from band.adapters.codex.narration import TurnRef, token_usage
from band.integrations.codex.types import CodexTokenUsage
from tests.adapters.codex.kit import ROOM_ID, ToolSchemaFakeTools

REF = TurnRef(ROOM_ID, "thread-x", "turn-1")


class TestTokenUsageEmission:
    """A thread's token usage is reported only once Codex has reported some."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("usage", [None, CodexTokenUsage()], ids=["none", "zero"])
    async def test_nothing_is_reported_before_real_usage(
        self, usage: CodexTokenUsage | None
    ) -> None:
        tools = ToolSchemaFakeTools()

        await token_usage(tools, REF, usage)

        assert tools.events_sent == []
