"""Real tool boundaries the activity suites drive Band tools through."""

from __future__ import annotations

import asyncio
from collections.abc import Hashable
from typing import Any

from band_rest.types.agent_activity_detail import AgentActivityDetail
from mcp.server.fastmcp import FastMCP

from band.integrations.mcp.engine import (
    EmbeddedResolver,
    EngineSpec,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
)
from band.runtime.tools.types import ToolDefinition
from band.testing.activity import ActivityRecorder


def band_tool_server(
    tools: Any, definition: ToolDefinition, *, request_ids: list[Any] | None = None
) -> FastMCP:
    """An MCP engine serving ``definition`` over ``tools``, noting each request id."""

    def resolve(room_id: str) -> Any:
        if request_ids is not None:
            request_ids.append(server.get_context().request_context.request_id)
        return tools

    registration = build_tool_registration(
        definition,
        extend_with_chat_id(definition.input_model, None),
        resolver=EmbeddedResolver(get_tools=resolve),
        strip_chat_id=True,
    )
    server = build_engine(EngineSpec(name="activity", tools=(registration,)))
    return server


class UnreachableObserver(ActivityRecorder):
    """Records each start and end, then fails to deliver it."""

    async def start(self, key: Hashable, activity: AgentActivityDetail) -> None:
        await super().start(key, activity)
        raise RuntimeError("activity endpoint down")

    async def end(self, key: Hashable) -> None:
        await super().end(key)
        raise RuntimeError("activity endpoint down")


class CancelledObserver(ActivityRecorder):
    """A report that is cancelled before it lands."""

    async def start(self, key: Hashable, activity: AgentActivityDetail) -> None:
        raise asyncio.CancelledError

    async def end(self, key: Hashable) -> None:
        raise asyncio.CancelledError


class ConjuringProxy:
    """Answers every attribute dynamically, as a mock or proxy would, and
    conjures a recording observer it never declared."""

    def __init__(self, conjured: ActivityRecorder) -> None:
        self.conjured = conjured
        self.events_sent: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name == "_activity_observer":
            return self.conjured
        raise AttributeError(name)

    async def send_event(self, content: str, message_type: str, **_: Any) -> str:
        self.events_sent.append(content)
        return "evt-1"
