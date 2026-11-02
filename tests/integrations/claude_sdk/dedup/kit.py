"""External send doubles for deduplication tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from band.testing import FakeAgentTools


def _make_inner() -> MagicMock:
    """Inner AgentToolsProtocol stub. send_message returns a unique result."""
    inner = MagicMock()
    inner.send_message = AsyncMock(return_value={"id": "msg-1"})
    inner.send_event = AsyncMock(return_value={"id": "evt-1"})
    inner.send_failure = AsyncMock(return_value={"id": "evt-2"})
    inner.add_participant = AsyncMock(return_value={"id": "u"})
    inner.participants = ["p1", "p2"]
    return inner


class GatedSendTools(FakeAgentTools):
    """Tools whose send stays in flight until ``gate`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> Any:
        self.entered.set()
        await self.gate.wait()
        return await super().send_message(content, mentions)
