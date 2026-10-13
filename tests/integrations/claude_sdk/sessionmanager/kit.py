"""Shared doubles and helpers for the ClaudeSessionManager tests."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ThinkingBlock,
)
from claude_agent_sdk.types import (
    CanUseTool,
    PermissionResult,
    PermissionResultAllow,
    ToolPermissionContext,
)

from band.integrations.claude_sdk.session_manager import (
    ClaudeRoomSession,
    ClaudeSessionManager,
)
from tests.adapters.claude_sdk.fakecli import Thinking
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET
from tests.adapters.claude_sdk.process import WorkspacePeer

# A result for work the CLI finished after the turn that started it ended.
STRAY_RESULT = {
    "type": "result",
    "subtype": "success",
    "duration_ms": 1,
    "duration_api_ms": 1,
    "is_error": False,
    "num_turns": 1,
    "session_id": "background",
    "result": "background work finished",
}


def thinking(text: str) -> list[Thinking]:
    """A turn that only thinks, so it needs no Band MCP server."""
    return [Thinking(text)]


def thoughts(messages: list[Any]) -> list[str]:
    return [
        block.thinking
        for message in messages
        for block in getattr(message, "content", [])
        if isinstance(block, ThinkingBlock)
    ]


def new_manager(**options: Any) -> ClaudeSessionManager:
    return ClaudeSessionManager(
        ClaudeAgentOptions(model="claude-fake", mcp_servers={}),
        retention=REPORTING_BUDGET,
        **options,
    )


class TaskRecordingPeer(WorkspacePeer):
    """A real CLI child that notes which task drives each transport call."""

    tasks: dict[str, asyncio.Task[Any] | None]

    async def connect(self) -> None:
        self.tasks = {"connect": asyncio.current_task()}
        await super().connect()

    async def write(self, data: str) -> None:
        if json.loads(data)["type"] == "user":
            self.tasks["query"] = asyncio.current_task()
        await super().write(data)

    async def close(self) -> None:
        self.tasks.setdefault("close", asyncio.current_task())
        await super().close()


def allow_everything(session: ClaudeRoomSession, generation: int) -> CanUseTool:
    async def decide(
        tool_name: str, arguments: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        return PermissionResultAllow(updated_input=arguments)

    return decide


class Signal(logging.Handler):
    """Sets ``seen`` once a record containing ``fragment`` is logged."""

    def __init__(self, fragment: str) -> None:
        super().__init__(logging.DEBUG)
        self.fragment = fragment
        self.seen = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if self.fragment in record.getMessage():
            self.seen.set()
