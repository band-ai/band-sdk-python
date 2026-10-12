"""Room workspace isolation for coding-agent adapters."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig, CodexSessionState
from band.core.protocols import AgentToolsProtocol
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.codex import CodexRequestMethod
from band.testing import FakeAgentTools
from band.workspaces import is_host_absolute, resolve_room_workspace


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


@pytest.mark.parametrize("spell", [str, Path.as_posix], ids=["native", "posix"])
def test_custom_workspace_is_created_on_first_resolution(
    tmp_path: Path, spell: Callable[[Path], str]
) -> None:
    workspace = tmp_path / "nested" / "room-a"

    resolved = resolve_room_workspace("room-a", lambda _room_id: spell(workspace))

    assert Path(resolved) == workspace
    assert workspace.is_dir()


@pytest.mark.parametrize(
    ("path", "accepted_on"),
    [
        pytest.param("workspace", set(), id="relative"),
        pytest.param("/workspace", {"posix"}, id="rooted-without-drive"),
        pytest.param(r"\workspace", set(), id="backslash-rooted"),
        pytest.param(r"C:\workspace", {"nt"}, id="drive"),
        pytest.param("C:/workspace", {"nt"}, id="drive-forward-slashes"),
        pytest.param(r"C:workspace", set(), id="drive-relative"),
        pytest.param(r"\\server\share\workspace", {"nt"}, id="unc"),
    ],
)
def test_is_host_absolute_follows_the_running_os(
    path: str, accepted_on: set[str]
) -> None:
    assert is_host_absolute(path) is (os.name in accepted_on)


@pytest.mark.asyncio
async def test_adapters_refuse_a_relative_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    codex = CodexAdapter(
        CodexAdapterConfig(workspace_for_room=lambda _room_id: "workspace")
    )
    acp = ACPClientAdapter(
        ACPClientAdapterConfig(command="codex"),
        workspace_for_room=lambda _room_id: "workspace",
    )

    with pytest.raises(ValueError, match="must return an absolute path"):
        codex._room_client("room-a")
    with pytest.raises(ValueError, match="must return an absolute path"):
        await acp._runtime_for("room-a")
    assert not (tmp_path / "workspace").exists()


@pytest.mark.asyncio
async def test_adapters_reject_a_custom_workspace_shared_by_live_rooms(
    tmp_path: Path,
) -> None:
    workspace = str(tmp_path / "workspace")

    def resolver(_room_id: str) -> str:
        return workspace

    codex = CodexAdapter(CodexAdapterConfig(workspace_for_room=resolver))
    acp = ACPClientAdapter(
        ACPClientAdapterConfig(command="codex"), workspace_for_room=resolver
    )

    codex._room_client("room-a")
    await acp._runtime_for("room-a")

    with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
        codex._room_client("room-b")
    with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
        await acp._runtime_for("room-b")


@pytest.mark.asyncio
async def test_model_override_survives_codex_client_rebuild() -> None:
    class Client:
        def __init__(self, model: str) -> None:
            self.model = model
            self.model_list_calls = 0

        async def connect(self) -> None:
            return None

        async def initialize(self, **_kwargs: object) -> None:
            return None

        async def request(
            self, method: str, _params: dict[str, object]
        ) -> dict[str, object]:
            assert method == CodexRequestMethod.MODEL_LIST
            self.model_list_calls += 1
            return {"data": [{"id": self.model, "hidden": False}]}

        async def close(self) -> None:
            return None

    clients = [Client("auto-model-a"), Client("auto-model-b")]

    def build_client(_config: object) -> Client:
        return clients.pop(0)

    adapter = CodexAdapter(CodexAdapterConfig(model=None))
    adapter._build_client = build_client  # type: ignore[method-assign]
    adapter._room_client("room-a")
    adapter._active_room.set("room-a")
    state = adapter._require_active_client_state()
    state.model_override = "room-model"

    await adapter._ensure_client_ready()
    adapter._client = None
    adapter._initialized = False
    await adapter._ensure_client_ready()

    assert adapter._selected_model == "room-model"
    assert clients == []


def test_codex_rejects_the_former_shared_cwd_option() -> None:
    with pytest.raises(ValueError, match="workspace_for_room or the default"):
        CodexAdapter(CodexAdapterConfig(cwd="/workspace"))


def test_codex_config_ignores_an_ambient_codex_cwd_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CODEX_CWD`` is a plausible ambient env var (a Codex CLI convention,
    this repo's own E2E harness) unrelated to this banned kwarg; it must never
    silently populate ``cwd`` and trip the rejection above."""
    monkeypatch.setenv("CODEX_CWD", "/some/unrelated/directory")

    config = CodexAdapterConfig(workspace_for_room=lambda _room_id: "/tmp/x")

    assert config.cwd is None
    CodexAdapter(config)  # must not raise


@pytest.mark.asyncio
async def test_codex_starts_each_thread_in_its_room_workspace(tmp_path: Path) -> None:
    class Client:
        def __init__(self) -> None:
            self.params: dict[str, object] | None = None

        async def request(
            self, method: str, params: dict[str, object]
        ) -> dict[str, object]:
            assert method == CodexRequestMethod.THREAD_START
            self.params = params
            return {"thread": {"id": "thread"}}

    def workspace_for_room(room_id: str) -> str:
        return str(tmp_path / room_id)

    adapter = CodexAdapter(
        CodexAdapterConfig(
            model="gpt-5.5",
            workspace_for_room=workspace_for_room,
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
        resolve_room_workspace("room-a", workspace_for_room),
        resolve_room_workspace("room-b", workspace_for_room),
    ]
