"""A host can handshake its configured ACP backend without claiming a room."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from acp.schema import ClientCapabilities

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.integrations.acp import client_adapter
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from tests.integrations.acp.acp_toolkit.harness import FakeSpawn
from tests.integrations.acp.acp_toolkit.peer import (
    PEER_EXIT_CODE,
    ExitStage,
    stdio_peer_config,
)
from tests.integrations.acp.acp_toolkit.probe import ProbeProcesses, ProbeSpawn


class TestACPPreflight:
    @pytest.mark.parametrize("backend", ["generic", "cursor", "copilot"])
    async def test_handshakes_without_room_session_or_model_turn(
        self, backend: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        command = ("configured-acp", "--transport", "stdio")
        if backend == "cursor":
            adapter = CursorACPAdapter(
                CursorACPAdapterConfig(command=command, api_key="cursor-key")
            )
        elif backend == "copilot":
            adapter = CopilotACPAdapter(
                CopilotACPAdapterConfig(command=command, github_token="copilot-key")
            )
        else:
            adapter = ACPClientAdapter(ACPClientAdapterConfig(command=command))
        spawn = ProbeSpawn()
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)
        monkeypatch.setattr(client_adapter.shutil, "which", lambda command: None)

        result = await adapter.preflight(timeout_s=1)

        assert result.ok, result.reason
        assert spawn.open_count == 0
        assert spawn.commands == [command]
        assert spawn.connection.new_session.await_count == 0
        assert spawn.connection.prompt.await_count == 0
        assert adapter._rooms == {}
        assert not tuple(tmp_path.iterdir())
        if backend == "cursor":
            assert spawn.environments == [{"CURSOR_API_KEY": "cursor-key"}]
        elif backend == "copilot":
            assert spawn.environments == [{"GITHUB_TOKEN": "copilot-key"}]

    async def test_preserves_capabilities_auth_environment_and_root_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        capabilities = ClientCapabilities()
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(
                command=("configured-acp",),
                cwd=str(tmp_path),
                env={"PROVIDER_KEY": "configured-key"},
                auth_method="interactive-login",
                use_unstable_protocol=True,
            ),
            client_capabilities=capabilities,
        )
        spawn = ProbeSpawn()
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)
        monkeypatch.setattr(client_adapter.shutil, "which", lambda command: None)

        result = await adapter.preflight(timeout_s=1)

        assert result.ok, result.reason
        assert spawn.environments == [{"PROVIDER_KEY": "configured-key"}]
        assert spawn.directories == [str(tmp_path)]
        assert spawn.unstable_protocols == [True]
        spawn.connection.initialize.assert_awaited_once_with(
            protocol_version=1, client_capabilities=capabilities
        )
        spawn.connection.authenticate.assert_awaited_once_with(
            method_id="interactive-login"
        )
        assert not tuple(tmp_path.iterdir())

    async def test_does_not_call_a_room_workspace_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def room_workspace(room_id: str) -> str:
            pytest.fail(f"preflight claimed a Band room: {room_id}")

        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command=("configured-acp",)),
            workspace_for_room=room_workspace,
        )
        monkeypatch.setattr(client_adapter, "spawn_agent_process", FakeSpawn())
        assert (await adapter.preflight(timeout_s=1)).ok

    @pytest.mark.parametrize("phase", ["spawn", "initialize", "authenticate"])
    async def test_reports_failed_handshakes_after_closing(
        self, phase: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command=("configured-acp",), auth_method="login")
        )
        spawn = ProbeSpawn()
        error = FileNotFoundError("configured-acp not installed")
        if phase == "spawn":
            spawn.start_error = error
        else:
            getattr(spawn.connection, phase).side_effect = error
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)

        result = await adapter.preflight(timeout_s=1)

        assert not result.ok
        assert result.reason
        assert result.remedy
        assert spawn.open_count == 0

    @pytest.mark.parametrize("phase", ["initialize", "authenticate"])
    async def test_bounds_handshake_and_allows_a_second_probe(
        self, phase: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command=("configured-acp",), auth_method="login")
        )
        spawn = ProbeSpawn()
        getattr(spawn.connection, phase).side_effect = asyncio.CancelledError
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)

        with pytest.raises(asyncio.CancelledError):
            await adapter.preflight(timeout_s=1)
        assert spawn.open_count == 0
        getattr(spawn.connection, phase).side_effect = None

        assert (await adapter.preflight(timeout_s=1)).ok
        assert spawn.open_count == 0

    @pytest.mark.looptime
    async def test_timeout_is_a_failure_and_closes_the_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Windows' native timer resolution can exceed this virtual deadline.
        monkeypatch.setattr(asyncio.get_running_loop(), "_clock_resolution", 1e-6)
        adapter = ACPClientAdapter(ACPClientAdapterConfig(command=("configured-acp",)))
        spawn = ProbeSpawn()
        spawn.connection.initialize.side_effect = spawn.hold_initialize
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)

        result = await adapter.preflight(timeout_s=0.01)

        assert spawn.initializing.is_set()
        assert not result.ok
        assert result.reason
        assert result.remedy
        assert spawn.open_count == 0
        spawn.connection.initialize.side_effect = None
        assert (await adapter.preflight(timeout_s=1)).ok

    async def test_keeps_failed_close_for_adapter_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = ACPClientAdapter(stdio_peer_config(ExitStage.EOF, []))
        spawn = ProbeProcesses(refuse_close=True)
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)
        try:
            with pytest.raises(CleanupIncompleteError):
                await adapter.preflight(timeout_s=10)
            process = spawn.children[0].process
            assert process is not None
            assert process.returncode is None

            await adapter.cleanup_all()

            assert process.returncode is not None
            spawn.refuse_close = False
            assert (await adapter.preflight(timeout_s=5)).ok
        finally:
            await spawn.close()

    async def test_retains_runtime_created_after_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = ACPClientAdapter(stdio_peer_config(ExitStage.EOF, []))
        spawn = ProbeProcesses(hold_creation=True)
        monkeypatch.setattr(client_adapter, "spawn_agent_process", spawn)
        try:
            with pytest.raises(CleanupIncompleteError):
                await adapter.preflight(timeout_s=0.1)
            child = spawn.children[0]
            await child.opened.wait()
            assert child.process is not None
            assert child.process.returncode is None
            assert child.release_creation is not None
            child.release_creation.set()

            await adapter.cleanup_all()

            assert child.process.returncode is not None
            assert adapter._rooms == {}
        finally:
            await spawn.close()

    async def test_speaks_the_current_acp_protocol_without_creating_a_session(
        self,
    ) -> None:
        adapter = ACPClientAdapter(stdio_peer_config(ExitStage.EOF, []))
        try:
            assert (await adapter.preflight(timeout_s=5)).ok
            assert (await adapter.preflight(timeout_s=5)).ok
        finally:
            await adapter.cleanup_all()

    async def test_failed_process_diagnostics_do_not_expose_credentials_or_urls(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caplog.set_level(logging.DEBUG)
        secret = "unstructured-provider-secret"
        private_url = "https://private.provider.invalid/api"
        config = stdio_peer_config(ExitStage.INITIALIZE, [f"{secret} {private_url}"])
        adapter = ACPClientAdapter(config)
        processes = ProbeProcesses()
        monkeypatch.setattr(client_adapter, "spawn_agent_process", processes)

        result = await adapter.preflight(timeout_s=5)

        assert not result.ok
        assert len(processes.children) == 1
        child = processes.children[0]
        assert child.opened.is_set()
        assert child.process is not None
        assert child.process.returncode == PEER_EXIT_CODE
        assert secret not in caplog.text
        assert private_url not in caplog.text
        assert secret not in result.model_dump_json()
        assert private_url not in result.model_dump_json()

    async def test_cleanup_attempts_probe_and_rooms_even_when_both_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = ACPClientAdapter(ACPClientAdapterConfig(command=("configured-acp",)))
        probe_close = AsyncMock(side_effect=CleanupIncompleteError("probe held"))

        async def close_rooms(stop: Awaitable[None], **kwargs: object) -> None:
            await stop
            raise CleanupIncompleteError("room held")

        room_close = AsyncMock(side_effect=close_rooms)
        monkeypatch.setattr(adapter._harness_probe, "close", probe_close)
        monkeypatch.setattr(adapter._mcp, "close_after", room_close)

        with pytest.raises(BaseExceptionGroup) as raised:
            await adapter.cleanup_all()

        assert {str(error) for error in raised.value.exceptions} == {
            "probe held",
            "room held",
        }
        probe_close.assert_awaited_once()
        room_close.assert_awaited_once()
