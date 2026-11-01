"""A Parlant tool server driven the way Parlant's engine drives it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import httpx

# Fixed ids for the tool context; tools find their room by session alone.
AGENT_ID = "agent-1"
CUSTOMER_ID = "customer-1"
SESSION_ID = "session-1"


@dataclass(frozen=True)
class ToolServer:
    """A running ``PluginServer`` and an HTTP client pointed at it."""

    server: Any
    client: httpx.AsyncClient

    async def enable(self, entries: list[Any]) -> None:
        for entry in entries:
            await self.server.enable_tool(entry)

    async def advertised(self, name: str) -> dict[str, Any]:
        """The tool as the engine reads it before deciding on a call."""
        response = await self.client.get(
            f"/tools/{name}/resolve",
            params={
                "agent_id": AGENT_ID,
                "session_id": "resolve",
                "customer_id": CUSTOMER_ID,
            },
        )
        response.raise_for_status()
        return response.json()["tool"]

    async def call(
        self, name: str, *, session_id: str, arguments: dict[str, Any]
    ) -> Any:
        """Call *name* with engine-shaped *arguments*; return the result data.

        The engine sends every argument as a string, and ``None`` for each
        optional the model omitted.
        """
        from parlant.core.services.tools.plugins import PluginClient  # noqa: PLC0415 -- optional provider dependency
        from parlant.core.tools import ToolContext  # noqa: PLC0415 -- optional provider dependency

        async with PluginClient(
            self.server.url,
            SimpleNamespace(create_event_emitter=AsyncMock(return_value=MagicMock())),
            MagicMock(),
            MagicMock(),
        ) as client:
            result = await client.call_tool(
                name, ToolContext(AGENT_ID, session_id, CUSTOMER_ID), arguments
            )
        return result.data
