"""Probe and room cleanup both retain custody until their transports close."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig, OmpRoom
from band.core.exceptions import CleanupIncompleteError
from band.integrations.omprpc import OmpRpcClient


@pytest.mark.asyncio
class TestOmpProbeCleanup:
    async def test_failed_probe_and_room_close_keep_both_resources_and_workspace(
        self, tmp_path: Path
    ) -> None:
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)))
        probe_close = AsyncMock(
            side_effect=[
                OSError("probe initial close"),
                OSError("probe retry close"),
                None,
            ]
        )

        async def discover() -> None:
            adapter._harness_probe.own(probe_close)

        with pytest.raises(CleanupIncompleteError):
            await adapter._harness_probe.run(discover, timeout_s=5)
        runtime = AsyncMock(spec=OmpRpcClient)
        runtime.close.side_effect = [OSError("room close"), None]
        closed_resources: list[str] = []
        resources = ExitStack()
        resources.callback(closed_resources.append, "closed")
        workspace = adapter._workspaces.claim("existing-room", str(tmp_path))
        room = OmpRoom(runtime=runtime, workspace=workspace, resources=resources)
        adapter._rooms["existing-room"] = room
        try:
            with pytest.raises(BaseExceptionGroup) as failure:
                await adapter.cleanup_all()
            assert len(failure.value.exceptions) == 2
            assert probe_close.await_count == 2
            assert runtime.close.await_count == 1
            assert adapter._rooms.get("existing-room") is room
            assert closed_resources == []
            with pytest.raises(ValueError, match="both"):
                adapter._workspaces.claim("other-room", workspace)

            await adapter.cleanup_all()
            assert probe_close.await_count == 3
            assert runtime.close.await_count == 2
            assert closed_resources == ["closed"]
            assert not adapter._rooms
            assert "existing-room" not in adapter._workspaces.rooms
            assert adapter._workspaces.claim("other-room", workspace) == workspace
            adapter._workspaces.release("other-room")
        finally:
            await adapter.cleanup_all()
