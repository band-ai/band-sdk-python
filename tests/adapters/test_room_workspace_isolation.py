"""Room-owned workspace guards for coding-agent adapters."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.integrations.acp.client_adapter import ACPClientAdapter


def test_codex_rejects_a_workspace_shared_by_live_rooms() -> None:
    adapter = CodexAdapter(
        CodexAdapterConfig(workspace_for_room=lambda _room_id: "/workspace")
    )

    adapter._room_client("room-a")

    with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
        adapter._room_client("room-b")


@pytest.mark.asyncio
async def test_acp_retries_workspace_resolution_after_a_failure() -> None:
    attempts = 0

    def workspace_for_room(_room_id: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("workspace provisioning failed")
        return "/workspace/room-a"

    adapter = ACPClientAdapter(command="codex", workspace_for_room=workspace_for_room)

    with pytest.raises(ValueError, match="workspace provisioning failed"):
        await adapter._runtime_for("room-a")

    runtime = await adapter._runtime_for("room-a")

    assert adapter._runtimes["room-a"] is runtime
    assert adapter._room_workspaces == {"room-a": "/workspace/room-a"}


@pytest.mark.asyncio
async def test_acp_rejects_a_workspace_shared_by_live_rooms() -> None:
    adapter = ACPClientAdapter(
        command="codex", workspace_for_room=lambda _room_id: "/workspace"
    )

    await adapter._runtime_for("room-a")

    with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
        await adapter._runtime_for("room-b")


@pytest.mark.asyncio
async def test_codex_discards_a_client_after_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        closed = False

        async def connect(self) -> None:
            raise RuntimeError("startup failed")

        async def close(self) -> None:
            self.closed = True

    client = FailingClient()
    adapter = CodexAdapter(
        CodexAdapterConfig(workspace_for_room=lambda _room_id: "/workspace/room-a")
    )
    adapter._room_client("room-a")
    adapter._active_room.set("room-a")
    monkeypatch.setattr(adapter, "_build_client", lambda _config: client)

    with pytest.raises(RuntimeError, match="startup failed"):
        await adapter._ensure_client_ready()

    assert client.closed
    assert adapter._client is None
