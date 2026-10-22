"""Required MCP connections cannot be inferred from an otherwise ready OMP process."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.integrations.ompprocess import (
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


class TestRequiredMcpStartup:
    @pytest.mark.parametrize(
        ("receipt", "reason"),
        [
            (None, "no valid SDK startup receipt"),
            ("not JSON", "no valid SDK startup receipt"),
            (
                {"systemPromptReady": True, "servers": []},
                "did not acknowledge every configured MCP server",
            ),
            (
                {
                    "systemPromptReady": True,
                    "servers": [
                        {"type": "connected", "serverName": "band-sdk:required"},
                        {"type": "connected", "serverName": "band-sdk:required"},
                    ],
                },
                "did not acknowledge every configured MCP server",
            ),
            (
                {
                    "systemPromptReady": True,
                    "servers": [{"type": "connected", "serverName": "unrelated"}],
                },
                "unexpected MCP startup receipt",
            ),
            (
                {
                    "systemPromptReady": True,
                    "servers": [{"type": "failed", "serverName": "band-sdk:required"}],
                },
                "no valid SDK startup receipt",
            ),
            (
                {
                    "systemPromptReady": True,
                    "servers": [
                        {
                            "type": "failed",
                            "serverName": "band-sdk:required",
                            "error": "initialize rejected",
                        }
                    ],
                },
                "band-sdk:required failed: initialize rejected",
            ),
            (
                {
                    "systemPromptReady": False,
                    "servers": [
                        {"type": "connected", "serverName": "band-sdk:required"}
                    ],
                },
                "required Band connection instructions",
            ),
        ],
    )
    async def test_rejects_incomplete_or_failed_receipts(
        self,
        tmp_path: Path,
        receipt: str | dict[str, object] | None,
        reason: str,
    ) -> None:
        plugin = tmp_path / "band-sdk"
        plugin.mkdir()
        if receipt is not None:
            payload = json.dumps(receipt) if isinstance(receipt, dict) else receipt
            (plugin / "receipt.json").write_text(payload, encoding="utf-8")
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.request.return_value = {
            "commands": [{"name": omp_mcp_ready_command(str(plugin))}],
        }
        with pytest.raises(OmpStartupError, match=reason):
            await omp_mcp_ready(
                runtime,
                str(plugin),
                {"required": OmpMcpServer(command="server")},
            )

    async def test_missing_startup_extension_cannot_fall_through_to_inference(
        self, tmp_path: Path
    ) -> None:
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.request.return_value = {"commands": []}
        with pytest.raises(OmpStartupError, match="required SDK startup extension"):
            await omp_mcp_ready(runtime, str(tmp_path), {})
        runtime.prompt.assert_not_awaited()

    async def test_complete_receipt_prompts_the_readiness_command(
        self, tmp_path: Path
    ) -> None:
        plugin = tmp_path / "band-sdk"
        plugin.mkdir()
        command = omp_mcp_ready_command(str(plugin))
        receipt = json.dumps(
            {
                "systemPromptReady": True,
                "servers": [{"type": "connected", "serverName": "band-sdk:required"}],
            }
        )
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.request.return_value = {"commands": [{"name": command}]}

        async def prompt(message: str, **_callbacks: object) -> None:
            _ = message
            (plugin / "receipt.json").write_text(receipt, encoding="utf-8")

        runtime.prompt.side_effect = prompt
        await omp_mcp_ready(
            runtime,
            str(plugin),
            {"required": OmpMcpServer(command="server")},
        )
        runtime.prompt.assert_awaited_once()
        assert runtime.prompt.await_args is not None
        assert runtime.prompt.await_args.args[0] == f"/{command}"

    @pytest.mark.parametrize(
        ("invoke", "reason"),
        [
            ("event", "invoked an agent"),
            ("tool", "invoked a host tool"),
            ("ui", "requested UI"),
        ],
    )
    async def test_startup_callbacks_cannot_fall_through_to_inference(
        self, tmp_path: Path, invoke: str, reason: str
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
        with pytest.raises(OmpStartupError, match=reason):
            await omp_mcp_ready(runtime, str(tmp_path), {})
