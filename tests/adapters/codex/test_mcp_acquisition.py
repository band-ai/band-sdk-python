"""External MCP resolution follows the retained room's acquisition boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.integrations.codex.mcp import CodexMcpHttpServer, CodexMcpStartupError
from tests.adapters.codexturns import FakeCodexClient


class TestMcpAcquisition:
    async def test_resolves_once_per_process_before_readiness(
        self, tmp_path: Path
    ) -> None:
        resolved: list[str] = []

        async def resolve(workspace: str):
            resolved.append(workspace)
            return {"private": CodexMcpHttpServer("http://localhost/mcp")}

        adapter = CodexAdapter(
            CodexAdapterConfig(workspace_for_room=lambda _: str(tmp_path)),
            resolve_mcp_servers=resolve,
        )
        client = FakeCodexClient()
        adapter._build_client = lambda _room, _launch: client
        room = adapter._room_client("a")
        try:
            with pytest.raises(CodexMcpStartupError):
                async with room.retained.use():
                    pass
            assert resolved == [str(tmp_path)]
            assert "mcpServerStatus/list" in client.request_methods
        finally:
            await adapter.cleanup_all()
