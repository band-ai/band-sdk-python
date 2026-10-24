"""Codex app-server thread items, shaped as its v2 protocol schema sends them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex import RpcEvent
from band.integrations.codex.types import CodexItemType
from band.testing.activity import ActivityRecorder
from tests.adapters.codex.kit import (
    CodexRoom,
    ToolSchemaFakeTools,
    parked_on_approval,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    event_notification,
    final_text,
    make_codex_adapter,
    turn_completed,
)

IN_PROGRESS = "inProgress"

LIST_FILES: dict[str, Any] = {
    "type": CodexItemType.COMMAND_EXECUTION,
    "id": "cmd-1",
    "command": "ls",
    "commandActions": [],
    "cwd": "/workspace",
    "status": IN_PROGRESS,
}
DOCS_LOOKUP: dict[str, Any] = {
    "type": CodexItemType.MCP_TOOL_CALL,
    "id": "mcp-1",
    "server": "docs",
    "tool": "search",
    "arguments": {"query": "activity"},
    "status": IN_PROGRESS,
}
BAND_TOOL_CALL: dict[str, Any] = {
    "type": CodexItemType.DYNAMIC_TOOL_CALL,
    "id": "dyn-1",
    "tool": "band_get_participants",
    "arguments": {},
    "status": IN_PROGRESS,
}
COMPACTION: dict[str, Any] = {"type": CodexItemType.CONTEXT_COMPACTION, "id": "cc-1"}


def item_started(item: dict[str, Any]) -> RpcEvent:
    return event_notification(
        "item/started", {"threadId": FIRST_THREAD_ID, "startedAtMs": 1, "item": item}
    )


def item_completed(item: dict[str, Any]) -> RpcEvent:
    """``item`` finished; an item with a lifecycle status completes it."""
    finished = {**item, "status": "completed"} if "status" in item else item
    return event_notification(
        "item/completed",
        {"threadId": FIRST_THREAD_ID, "completedAtMs": 2, "item": finished},
    )


class HeldApprovalClient(FakeCodexClient):
    """Hold the provider's approval answer at its transport boundary."""

    def __init__(self, *, response_error: Exception | None = None) -> None:
        self.response_error = response_error
        super().__init__(
            events=list(parked_on_approval(final_text("Done."), turn_completed()))
        )
        self.responding = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        self.responding.set()
        await self.release.wait()
        if self.response_error is not None:
            raise self.response_error
        await super().respond(request_id, result)


@dataclass
class ApprovalExchange:
    room: CodexRoom
    client: HeldApprovalClient

    async def finish(self) -> None:
        self.client.release.set()
        await self.room.settled()


@asynccontextmanager
async def held_approval_answer(
    recorder: ActivityRecorder, *, response_error: Exception | None = None
) -> AsyncIterator[ApprovalExchange]:
    client = HeldApprovalClient(response_error=response_error)
    adapter = make_codex_adapter(
        client,
        config=CodexAdapterConfig(approval_mode="manual", turn_settle_timeout_s=0),
    )
    await adapter.on_started("Agent", "Agent")
    room = CodexRoom(adapter, client)
    tools = recorder.observing(ToolSchemaFakeTools())
    try:
        await send_bootstrap(adapter, tools=tools)
        await tools.until_said("Approval requested")
        await room.send("/approve")
        await client.responding.wait()
        yield ApprovalExchange(room, client)
    finally:
        client.release.set()
        await adapter.cleanup_all()
