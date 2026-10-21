"""Required MCP connections cannot be inferred from an otherwise ready OMP process."""

from __future__ import annotations

import json
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
