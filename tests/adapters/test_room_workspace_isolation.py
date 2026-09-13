"""Room workspace isolation for coding-agent adapters."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexSessionState
from band.core.protocols import AgentToolsProtocol
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.testing import FakeAgentTools
from band.workspaces import resolve_room_workspace


def test_default_workspace_is_created_per_room(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    first = resolve_room_workspace("room-a", None)
    second = resolve_room_workspace("room-b", None)

    assert [first, second] == [
        str(tmp_path / ".band-workspaces" / "room-a"),
        str(tmp_path / ".band-workspaces" / "room-b"),
    ]
    assert Path(first).is_dir()
    assert Path(second).is_dir()


@pytest.mark.asyncio
async def test_adapters_reject_a_custom_workspace_shared_by_live_rooms() -> None:
    def resolver(_room_id: str) -> str:
        return "/workspace"

    codex = CodexAdapter(CodexAdapterConfig(workspace_for_room=resolver))
    acp = ACPClientAdapter(command="codex", workspace_for_room=resolver)

    codex._room_client("room-a")
    await acp._runtime_for("room-a")

    with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
        codex._room_client("room-b")
    with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
        await acp._runtime_for("room-b")


def test_codex_rejects_the_former_shared_cwd_option() -> None:
    with pytest.raises(ValueError, match="workspace_for_room or the default"):
        CodexAdapter(CodexAdapterConfig(cwd="/workspace"))


@pytest.mark.asyncio
async def test_codex_starts_each_thread_in_its_room_workspace() -> None:
    class Client:
        def __init__(self) -> None:
            self.params: dict[str, object] | None = None

        async def request(
            self, method: str, params: dict[str, object]
        ) -> dict[str, object]:
            assert method == "thread/start"
            self.params = params
            return {"thread": {"id": "thread"}}

    adapter = CodexAdapter(
        CodexAdapterConfig(
            model="gpt-5.5",
            workspace_for_room=lambda room_id: f"/workspace/{room_id}",
        )
    )
    tools = cast(AgentToolsProtocol, FakeAgentTools())
    clients: list[Client] = []
    for room_id in ("room-a", "room-b"):
        adapter._room_client(room_id)
        adapter._active_room.set(room_id)
        client = Client()
        adapter._client = client  # type: ignore[assignment]
        adapter._selected_model = "gpt-5.5"
        clients.append(client)
        await adapter._ensure_thread(
            room_id=room_id,
            history=CodexSessionState(),
            tools=tools,
            is_session_bootstrap=False,
        )

    assert [client.params["cwd"] for client in clients if client.params] == [
        "/workspace/room-a",
        "/workspace/room-b",
    ]
