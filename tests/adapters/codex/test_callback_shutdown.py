"""A custom tool can request shutdown without being cancelled by its own stop."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from band.adapters.codex import CodexAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.core.retention import RoomRetentionConfig
from tests.adapters.codex.kit import ToolSchemaFakeTools, send_bootstrap
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_request,
    make_codex_adapter,
    turn_completed,
)


class TestCallbackShutdown:
    async def test_enrolled_tool_gets_pending_cleanup_and_unwinds_without_cancellation(
        self,
    ) -> None:
        class ShutdownInput(BaseModel):
            """Request orderly host shutdown."""

        observed: list[str] = []

        async def shutdown(_input: ShutdownInput) -> str:
            try:
                await adapter.cleanup_all()
            except CleanupIncompleteError:
                observed.append("pending")
            except asyncio.CancelledError:
                observed.append("cancelled")
                raise
            return "shutdown requested"

        client = FakeCodexClient(
            events=[
                event_request(
                    99,
                    "item/tool/call",
                    {"tool": "shutdown", "arguments": {}, "callId": "shutdown-99"},
                ),
                turn_completed(),
            ]
        )
        adapter = make_codex_adapter(
            client,
            config=CodexAdapterConfig(
                turn_settle_timeout_s=0.05,
                retention=RoomRetentionConfig(transition_timeout_s=1),
            ),
            additional_tools=[(ShutdownInput, shutdown)],
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")
        try:
            results = await asyncio.gather(
                send_bootstrap(adapter, tools=tools), return_exceptions=True
            )
            assert observed == ["pending"]
            assert results == [None]
            assert client.responses[0][1]["success"] is True
            assert (
                client.responses[0][1]["contentItems"][0]["text"]
                == "shutdown requested"
            )
        finally:
            await adapter.cleanup_all()
        assert client.closed
