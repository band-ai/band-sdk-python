"""Room workspace isolation for coding-agent adapters."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.codex import CodexRequestMethod
from band.runtime.tools import BandTool
from band.workspaces import RoomWorkspaces, is_host_absolute, resolve_room_workspace
from tests.adapters.codex.kit import (
    PROCESS_EXITED,
    CodexRoom,
    answered_turn,
    patch_codex_clients_by_room,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    patch_codex_client,
    tool_call_request,
    turn_completed,
)


class TestRoomWorkspaceIsolation:
    def test_default_workspace_is_created_per_room(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
        self, tmp_path: Path, spell: Callable[[Path], str]
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
        self, path: str, accepted_on: set[str]
    ) -> None:
        assert is_host_absolute(path) is (os.name in accepted_on)

    def test_adapters_refuse_a_relative_workspace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
            acp._room("room-a")
        assert not (tmp_path / "workspace").exists()

    def test_adapters_reject_a_custom_workspace_shared_by_live_rooms(
        self,
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
        acp._room("room-a")

        with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
            codex._room_client("room-b")
        with pytest.raises(ValueError, match="both 'room-a' and 'room-b'"):
            acp._room("room-b")

    def test_a_failed_room_setup_gives_its_workspace_back(self, tmp_path: Path) -> None:
        workspaces = RoomWorkspaces(lambda _room_id: str(tmp_path / "workspace"))

        with pytest.raises(RuntimeError), workspaces.claiming("room-a"):
            raise RuntimeError("room setup failed")

        workspaces.claim("room-b")

        assert workspaces.rooms == ("room-b",)

    @pytest.mark.asyncio
    async def test_model_override_survives_codex_client_rebuild(self) -> None:
        lost = FakeCodexClient(events=[PROCESS_EXITED])
        replacement = FakeCodexClient(
            events=[tool_call_request(1, BandTool.NO_REPLY), turn_completed()]
        )
        adapter = CodexAdapter(CodexAdapterConfig(model=None))
        patch_codex_client(adapter, lost, replacement)
        await adapter.on_started("Codex Agent", "A coding agent")
        room = CodexRoom(adapter, lost)

        await room.send("/model room-model")
        with pytest.raises(TurnResultAlreadyReported):
            await room.send("work")
        await room.send("more work")

        (turn,) = replacement.params_of(CodexRequestMethod.TURN_START)
        assert turn["model"] == "room-model"

    def test_codex_rejects_the_former_shared_cwd_option(self) -> None:
        with pytest.raises(ValueError, match="workspace_for_room or the default"):
            CodexAdapter(CodexAdapterConfig(cwd="/workspace"))

    def test_codex_config_ignores_an_ambient_codex_cwd_env_var(
        self,
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
    async def test_codex_starts_each_thread_in_its_room_workspace(
        self, tmp_path: Path
    ) -> None:
        def workspace_for_room(room_id: str) -> str:
            return str(tmp_path / room_id)

        rooms = ("room-a", "room-b")
        clients = {
            room_id: FakeCodexClient(events=answered_turn()) for room_id in rooms
        }
        adapter = CodexAdapter(
            CodexAdapterConfig(workspace_for_room=workspace_for_room)
        )
        patch_codex_clients_by_room(adapter, clients)
        await adapter.on_started("Codex Agent", "A coding agent")

        for room_id in rooms:
            await send_bootstrap(adapter, room_id=room_id)

        assert [
            clients[room_id].params_of(CodexRequestMethod.THREAD_START)[0]["cwd"]
            for room_id in rooms
        ] == [resolve_room_workspace(room_id, workspace_for_room) for room_id in rooms]
