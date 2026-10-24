"""Required MCP connections cannot be inferred from an otherwise ready OMP process."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")

from band.integrations.ompprocess import (
    OMP_MCP_RECEIPT,
    OMP_PLUGIN_ROOT,
    McpConnectionStatus,
    OmpMcpServer,
    OmpStartupError,
    omp_mcp_ready,
    omp_mcp_ready_command,
)
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)

REQUIRED_SERVER = f"{OMP_PLUGIN_ROOT}:required"


class TestRequiredMcpStartup:
    @pytest.mark.parametrize(
        "receipt",
        [
            pytest.param(None, id="missing-receipt"),
            pytest.param("not JSON", id="malformed-receipt"),
            pytest.param(
                {"systemPromptReady": True, "servers": []},
                id="missing-server",
            ),
            pytest.param(
                {
                    "systemPromptReady": True,
                    "servers": [
                        {
                            "type": McpConnectionStatus.CONNECTED,
                            "serverName": REQUIRED_SERVER,
                        },
                        {
                            "type": McpConnectionStatus.CONNECTED,
                            "serverName": REQUIRED_SERVER,
                        },
                    ],
                },
                id="duplicate-server",
            ),
            pytest.param(
                {
                    "systemPromptReady": True,
                    "servers": [
                        {
                            "type": McpConnectionStatus.CONNECTED,
                            "serverName": "unrelated",
                        }
                    ],
                },
                id="unexpected-server",
            ),
            pytest.param(
                {
                    "systemPromptReady": True,
                    "servers": [
                        {
                            "type": McpConnectionStatus.FAILED,
                            "serverName": REQUIRED_SERVER,
                        }
                    ],
                },
                id="missing-failure-detail",
            ),
            pytest.param(
                {
                    "systemPromptReady": True,
                    "servers": [
                        {
                            "type": McpConnectionStatus.FAILED,
                            "serverName": REQUIRED_SERVER,
                            "error": "initialize rejected",
                        }
                    ],
                },
                id="failed-server",
            ),
            pytest.param(
                {
                    "systemPromptReady": False,
                    "servers": [
                        {
                            "type": McpConnectionStatus.CONNECTED,
                            "serverName": REQUIRED_SERVER,
                        }
                    ],
                },
                id="unloaded-instructions",
            ),
        ],
    )
    async def test_rejects_incomplete_or_failed_receipts(
        self,
        tmp_path: Path,
        receipt: str | dict[str, object] | None,
    ) -> None:
        plugin = tmp_path / OMP_PLUGIN_ROOT
        plugin.mkdir()
        if receipt is not None:
            payload = json.dumps(receipt) if isinstance(receipt, dict) else receipt
            (plugin / OMP_MCP_RECEIPT).write_text(payload, encoding="utf-8")
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.request.return_value = {
            "commands": [{"name": omp_mcp_ready_command(str(plugin))}],
        }
        with pytest.raises(OmpStartupError):
            await omp_mcp_ready(
                runtime,
                str(plugin),
                {"required": OmpMcpServer(command="server")},
            )

    async def test_missing_startup_extension_is_rejected(self, tmp_path: Path) -> None:
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.request.return_value = {"commands": []}
        with pytest.raises(OmpStartupError):
            await omp_mcp_ready(runtime, str(tmp_path), {})

    @pytest.mark.parametrize("invoke", ["event", "tool", "ui"])
    async def test_startup_callbacks_cannot_fall_through_to_inference(
        self, tmp_path: Path, invoke: str
    ) -> None:
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.request.return_value = {
            "commands": [{"name": omp_mcp_ready_command(str(tmp_path))}],
        }

        async def prompt(
            message: str,
            *,
            on_event: Callable[[OmpEvent], Awaitable[None]],
            on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
            on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
        ) -> None:
            _ = message
            match invoke:
                case "event":
                    await on_event(
                        OmpEvent.model_validate({"type": OmpEventType.AGENT_START})
                    )
                case "tool":
                    await on_tool(
                        OmpHostToolCall.model_validate(
                            {
                                "id": "host-1",
                                "toolCallId": "call-1",
                                "toolName": "band_chat",
                                "arguments": {},
                            }
                        )
                    )
                case "ui":
                    await on_ui(
                        OmpUIRequest.model_validate({"id": "ask", "method": "select"})
                    )

        runtime.prompt.side_effect = prompt
        with pytest.raises(OmpStartupError):
            await omp_mcp_ready(runtime, str(tmp_path), {})
