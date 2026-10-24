"""Publish native startup receipts before accepting ordinary prompts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import JsonValue

from band.integrations.ompprocess import (
    OMP_MCP_RECEIPT,
    OMP_PLUGIN_ROOT,
    ConnectedMcpServer,
    McpConnectionStatus,
    McpStartupReceipt,
    omp_mcp_ready_command,
)
from band.integrations.ompwire import (
    OmpCommandName,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.adapters.omp.kit import RecordingRuntime


class ReceiptRuntime(RecordingRuntime):
    """Answer native readiness with a chosen receipt from the startup plugin."""

    def __init__(self, plugin: Path, receipt_servers: tuple[str, ...] | None) -> None:
        super().__init__()
        self.plugin = plugin
        self.receipt_servers = receipt_servers

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        if command == OmpCommandName.GET_AVAILABLE_COMMANDS:
            return {"commands": [{"name": omp_mcp_ready_command(str(self.plugin))}]}
        return await super().request(command, **parameters)

    async def prompt(
        self,
        _message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        if _message == f"/{omp_mcp_ready_command(str(self.plugin))}":
            if self.receipt_servers is not None:
                receipt = McpStartupReceipt(
                    systemPromptReady=True,
                    servers=[
                        ConnectedMcpServer(
                            type=McpConnectionStatus.CONNECTED,
                            serverName=f"{OMP_PLUGIN_ROOT}:{name}",
                        )
                        for name in self.receipt_servers
                    ],
                )
                (self.plugin / OMP_MCP_RECEIPT).write_text(
                    receipt.model_dump_json(), encoding="utf-8"
                )
            return
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)
