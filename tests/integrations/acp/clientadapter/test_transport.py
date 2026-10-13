"""Tests for the injected stdio transport seam."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from tests.integrations.acp.acp_toolkit.harness import (
    inject_acp_spawn,
    started_runtime,
)
from tests.integrations.acp.clientadapter.kit import CODEX


class TestACPClientAdapterTransport:
    """Tests for the injected stdio transport seam."""

    @pytest.mark.asyncio
    async def test_injected_spawn_used_on_connection_start(
        self, make_acp_transport
    ) -> None:
        """FakeSpawn patched onto _build_runtime is used when a room connects."""
        transport = make_acp_transport()
        with patch(
            "band.integrations.acp.client_adapter.shutil.which", return_value=None
        ):
            adapter = ACPClientAdapter(CODEX)
            inject_acp_spawn(adapter, transport)
            await adapter.on_started("Codex", "Codex bridge")
            runtime = await started_runtime(adapter)
        assert runtime._conn is transport.conn
        args, _ = transport.last_call
        assert args == ("codex",)

    @pytest.mark.asyncio
    async def test_room_workspace_is_used_for_initial_spawn(
        self, make_acp_transport, tmp_path: Path
    ) -> None:
        transport = make_acp_transport()
        workspace = tmp_path / "acp-room"
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command="codex"),
            workspace_for_room=lambda _room_id: str(workspace),
        )
        inject_acp_spawn(adapter, transport)
        await adapter.on_started("", "")
        await started_runtime(adapter)

        assert transport.last_kwargs["cwd"] == str(workspace)
