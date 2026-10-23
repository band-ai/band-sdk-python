from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import band.integrations.ompprocess as omp_module
from band.adapters.omp import OMP_SESSION_DIRECTORY, OmpAdapter, OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.integrations.ompprocess import OmpMcpServer, OmpMcpTransport
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.omp.kit import (
    BandMcpReplyRuntime,
    Launches,
    checkpoint,
    launches,
    remote,
    requests_named,
    run_turn,
)
from tests.integrations.acp.conftest import make_platform_message

__all__ = ["launches"]


class TestMcpResolution:
    @pytest.mark.asyncio
    async def test_each_new_connection_uses_its_own_resolved_snapshot(
        self, launches: Launches, tmp_path: Path
    ):
        current = {"linear": remote("grant-A")}
        seen: list[str] = []

        async def resolve(workspace: str):
            seen.append(workspace)
            return dict(current)

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
        )
        await run_turn(adapter, FakeAgentTools())
        await adapter.on_cleanup("room-123")  # the native connection is retired
        current["linear"] = remote("grant-B")
        await run_turn(adapter, FakeAgentTools())

        assert [s["linear"]["headers"]["Authorization"] for s in launches.servers] == [
            "Bearer grant-A",
            "Bearer grant-B",
        ]
        claimed = os.path.realpath(tmp_path / "room-123")
        assert seen == [claimed, claimed]  # the claimed workspace, each time
        assert "grant" not in repr(adapter.config)
        assert adapter.config.mcp_servers == {}
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_reusing_a_live_connection_does_not_resolve_again(
        self, launches: Launches, tmp_path: Path
    ):
        resolve = AsyncMock(return_value={"linear": remote("grant-A")})
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
        )

        await run_turn(adapter, FakeAgentTools())
        await run_turn(adapter, FakeAgentTools())

        assert resolve.await_count == 1
        assert len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_resolved_set_replaces_static_servers_even_when_empty(
        self, launches: Launches, tmp_path: Path, monkeypatch
    ):
        ready = AsyncMock()
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), mcp_servers={"stale": remote("old")}),
            resolve_mcp_servers=AsyncMock(return_value={}),
            emit=(),
        )

        await run_turn(adapter, FakeAgentTools())

        assert launches.servers == [{}]
        assert ready.await_args.args[2] == {}  # readiness expects the same empty set
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_readiness_checks_the_same_snapshot_the_overlay_carries(
        self, launches: Launches, tmp_path: Path, monkeypatch
    ):
        ready = AsyncMock()
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
        snapshots = iter([{"linear": remote("grant-A")}, {"linear": remote("grant-B")}])

        async def resolve(_workspace: str):
            return next(snapshots)

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
        )
        await run_turn(adapter, FakeAgentTools())

        assert ready.await_count == 1
        assert ready.await_args.args[2] == {"linear": remote("grant-A")}
        assert (
            launches.servers[0]["linear"]["headers"]["Authorization"]
            == "Bearer grant-A"
        )
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_failed_resolution_launches_nothing_and_a_later_one_recovers(
        self, launches: Launches, tmp_path: Path
    ):
        resolve = AsyncMock(
            side_effect=[
                RuntimeError("grant unavailable"),
                {"linear": remote("grant-B")},
            ]
        )
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path), mcp_servers={"linear": remote("static")}
            ),
            resolve_mcp_servers=resolve,
            emit=(),
        )
        failed = FakeAgentTools()

        with pytest.raises(RuntimeError, match="grant unavailable"):
            await run_turn(adapter, failed)

        assert launches.runtimes == []  # no static fallback, no process
        assert failure_reports(failed)
        assert not adapter._workspaces.rooms  # the claim was released
        await run_turn(adapter, FakeAgentTools())
        assert [s["linear"]["headers"]["Authorization"] for s in launches.servers] == [
            "Bearer grant-B"
        ]
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resolver_settles", [False, True])
    async def test_shutdown_during_resolution_leaves_no_overlay_or_process(
        self, launches: Launches, tmp_path: Path, monkeypatch, resolver_settles: bool
    ):
        entered = asyncio.Event()
        overlays: list[str] = []
        real_plugin = omp_module.omp_mcp_plugin

        def recording_plugin(servers, **kwargs):
            overlays.append(kwargs["workspace"])
            return real_plugin(servers, **kwargs)

        monkeypatch.setattr("band.adapters.omp.omp_mcp_plugin", recording_plugin)

        async def resolve(_workspace: str):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not resolver_settles:
                    raise
                # A resolver may settle durable work and return instead of re-raising.
                await checkpoint()
            return {"linear": remote("late")}

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
        )
        turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        await asyncio.wait_for(entered.wait(), 1)

        await asyncio.wait_for(adapter.cleanup_all(), 1)

        assert turn.done()
        assert overlays == [] and launches.runtimes == []
        assert not adapter._workspaces.rooms
        with pytest.raises(asyncio.CancelledError):
            await turn

    @pytest.mark.asyncio
    async def test_a_blocked_resolution_does_not_hold_another_room(
        self, launches: Launches, tmp_path: Path
    ):
        release = asyncio.Event()
        slow_entered = asyncio.Event()

        async def resolve(workspace: str):
            if workspace.endswith("slow"):
                slow_entered.set()
                await release.wait()
            return {}

        adapter = OmpAdapter(
            resolve_mcp_servers=resolve,
            workspace_for_room=lambda room: str(tmp_path / room),
            emit=(),
        )
        for room in ("slow", "fast"):
            (tmp_path / room).mkdir()

        async def turn(room: str):
            await adapter.run_judged_turn(
                msg=make_platform_message("hello"),
                tools=FakeAgentTools(),
                history=OmpSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=room,
            )

        slow = asyncio.create_task(turn("slow"))
        await asyncio.wait_for(
            slow_entered.wait(), 1
        )  # slow is now inside its resolver
        await asyncio.wait_for(turn("fast"), 1)

        assert (
            not release.is_set() and not slow.done()
        )  # fast finished while slow blocked
        assert len(launches.runtimes) == 1
        release.set()
        await asyncio.wait_for(slow, 1)
        assert len(launches.runtimes) == 2
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_resolved_stdio_paths_are_anchored_in_the_claimed_workspace(
        self, launches: Launches, tmp_path: Path
    ):
        server = OmpMcpServer(
            command="./bin/tool", cwd="tools", env={"TOKEN": "s3cret"}
        )
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_mcp_servers=AsyncMock(return_value={"tool": server}),
            emit=(),
        )

        await run_turn(adapter, FakeAgentTools())

        (servers,) = launches.servers
        workspace = Path(os.path.realpath(tmp_path)) / "room-123"
        assert servers["tool"]["cwd"] == str(workspace / "tools")
        assert servers["tool"]["command"] == str(workspace / "tools" / "bin" / "tool")
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_resolved_server_cannot_take_the_band_tool_namespace(
        self, launches: Launches, tmp_path: Path
    ):
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_mcp_servers=AsyncMock(return_value={"band": remote("x")}),
            emit=(),
        )
        tools = FakeAgentTools()

        with pytest.raises(ValueError, match="reserved"):
            await run_turn(adapter, tools)

        assert launches.runtimes == []
        assert failure_reports(tools)

    @pytest.mark.asyncio
    async def test_a_restored_session_survives_a_resolved_connection(
        self, launches: Launches, tmp_path: Path
    ):
        session_dir = (
            Path(os.path.realpath(tmp_path)) / "room-123" / OMP_SESSION_DIRECTORY
        )
        session_dir.mkdir(parents=True)
        saved = session_dir / "saved.jsonl"
        saved.write_text("{}", encoding="utf-8")
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_mcp_servers=AsyncMock(return_value={"linear": remote("grant-A")}),
            emit=(),
        )

        async def bootstrap_turn():
            await adapter.run_judged_turn(
                msg=make_platform_message("hello"),
                tools=FakeAgentTools(),
                history=OmpSessionState(room_to_session={"room-123": str(saved)}),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        try:
            await bootstrap_turn()
            (runtime,) = launches.runtimes
            assert requests_named(runtime, OmpCommandName.SWITCH_SESSION) == [
                {"sessionPath": str(saved)}
            ]
            assert runtime.session_file == str(saved)
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("external", [{}, {"linear": remote("grant-A")}])
    async def test_the_configured_band_endpoint_survives_any_resolved_external_set(
        self, tmp_path: Path, monkeypatch, external
    ):
        overlays: list[dict[str, object]] = []

        def spawn(command, **_kwargs):
            plugin = Path(command[command.index("--plugin-dir") + 1])
            overlays.append(
                json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
            )
            return BandMcpReplyRuntime()

        ready = AsyncMock()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", spawn)
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", ready)
        band = OmpMcpServer(type=OmpMcpTransport.HTTP, url="http://127.0.0.1:9/band")
        adapter = OmpAdapter(
            OmpAdapterConfig(
                cwd=str(tmp_path),
                inject_band_tools=False,
                mcp_servers={"band": band, "stale": remote("old")},
            ),
            resolve_mcp_servers=AsyncMock(return_value=external),
            emit=(),
        )
        tools = FakeAgentTools()

        await run_turn(adapter, tools)

        expected = {"band", *external}
        (overlay,) = overlays
        assert (
            set(overlay["mcpServers"]) == expected
        )  # the cached external entry is gone
        assert overlay["mcpServers"]["band"]["url"] == "http://127.0.0.1:9/band"
        assert set(ready.await_args.args[2]) == expected
        assert (
            tools.turn.replied
        )  # Band's reply tool still routes through its own server
        await adapter.cleanup_all()
