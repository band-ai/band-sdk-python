from __future__ import annotations

import ast
import asyncio

import pytest

from band.integrations.mcp import (
    BandMCPBackendSettings,
    BandMCPTransport,
    SharedBandMCPBackend,
)
from band.integrations.mcp.backends import create_band_mcp_backend
from band.runtime.tools import iter_tool_definitions
from band.testing import FakeAgentTools
from tests.mcpclient import (
    FakeBandMCPBackend,
    backends_created_by,
    crash_backend,
    served_tool_names,
)
from tests.paths import REPO_ROOT, SRC_ROOT

ONE_TOOL = next(iter(iter_tool_definitions()))


def one_tool_settings() -> BandMCPBackendSettings:
    """A real server on the default port range."""
    return BandMCPBackendSettings(
        tool_definitions=[ONE_TOOL],
        get_tools=lambda _room_id: None,
    )


class TestBandMcpBackends:
    @pytest.mark.asyncio
    async def test_create_http_backend(self) -> None:
        tool_definitions = list(iter_tool_definitions())[:1]
        tools = FakeAgentTools()

        backend = await create_band_mcp_backend(
            BandMCPBackendSettings(
                tool_definitions=tool_definitions,
                get_tools=lambda room_id: tools if room_id == "room-123" else None,
            )
        )

        try:
            assert backend.allowed_tools == [f"mcp__band__{tool_definitions[0].name}"]
            assert backend.local_server.http_url.startswith("http://127.0.0.1:")
            assert backend.is_running
        finally:
            await backend.stop()
            assert backend.is_running is False


class TestSharedBandMCPBackend:
    async def test_concurrent_first_uses_start_one_backend(self) -> None:
        with backends_created_by() as starts:
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                first, second = await asyncio.gather(owner.ensure(), owner.ensure())

        assert first is second
        assert len(starts.requested) == 1

    async def test_a_crashed_backend_is_replaced_by_a_live_one(self) -> None:
        async with SharedBandMCPBackend(one_tool_settings) as owner:
            await owner.ensure()
            crashed = await crash_backend(owner)

            replacement = await owner.ensure()

            url = replacement.endpoint(BandMCPTransport.HTTP)
            assert replacement is not crashed
            assert crashed.endpoint(BandMCPTransport.HTTP) != url
            assert await served_tool_names(url) == {ONE_TOOL.name}

    async def test_a_replacement_avoids_the_dead_servers_port(self) -> None:
        with backends_created_by() as starts:
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                dead = await owner.ensure()
                dead.is_running = False

                await owner.ensure()

        assert starts.avoided == [None, dead.local_server.port]
        assert dead.stop_calls == 1

    async def test_a_failed_start_holds_nothing_and_the_next_use_retries(self) -> None:
        with backends_created_by(OSError("no free port")):
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                with pytest.raises(OSError):
                    await owner.ensure()

                assert owner.current is None
                assert await owner.ensure() is owner.current

    async def test_a_failed_replacement_keeps_the_dead_backend_for_the_next_use(
        self,
    ) -> None:
        dead = FakeBandMCPBackend()
        with backends_created_by(dead, OSError("no free port")) as starts:
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                await owner.ensure()
                dead.is_running = False

                with pytest.raises(OSError):
                    await owner.ensure()
                assert owner.current is dead

                assert await owner.ensure() is not dead

        assert starts.avoided[1:] == [dead.local_server.port] * 2

    async def test_a_final_close_refuses_every_use_until_reopened(self) -> None:
        with backends_created_by():
            owner = SharedBandMCPBackend(one_tool_settings)
            async with owner:
                await owner.ensure()
            with pytest.raises(RuntimeError, match="stopped"):
                await owner.ensure()

            await owner.close(final=False)
            with pytest.raises(RuntimeError, match="stopped"):
                await owner.ensure()

            await owner.reopen()
            assert await owner.ensure() is owner.current
            await owner.close(final=True)

    async def test_a_non_final_close_lets_the_next_use_start_afresh(self) -> None:
        with backends_created_by() as starts:
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                closed = await owner.ensure()
                await owner.close(final=False)

                fresh = await owner.ensure()

        assert fresh is not closed
        assert closed.stop_calls == 1
        assert len(starts.requested) == 2

    async def test_a_close_behind_an_in_flight_start_stops_what_it_started(
        self,
    ) -> None:
        """The start holds the lock; the final close waits for it, then stops
        the backend it produced, and a use queued after the close is refused."""
        started = FakeBandMCPBackend()
        release_start = asyncio.Event()

        async def slow_start() -> FakeBandMCPBackend:
            await release_start.wait()
            return started

        with backends_created_by(then=slow_start):
            owner = SharedBandMCPBackend(one_tool_settings)
            first_use = asyncio.create_task(owner.ensure())
            await asyncio.sleep(0)
            shutdown = asyncio.create_task(owner.close(final=True))
            late_use = asyncio.create_task(owner.ensure())
            await asyncio.sleep(0)
            release_start.set()

            first, _, late = await asyncio.gather(
                first_use, shutdown, late_use, return_exceptions=True
            )

        assert first is started
        assert started.stop_calls == 1
        assert isinstance(late, RuntimeError)

    async def test_a_slow_stop_never_holds_up_the_next_use(self) -> None:
        stop_started, stop_release = asyncio.Event(), asyncio.Event()
        stopping = FakeBandMCPBackend(
            stop_started=stop_started, stop_release=stop_release
        )
        with backends_created_by(stopping):
            async with SharedBandMCPBackend(one_tool_settings) as owner:
                await owner.ensure()
                shutdown = asyncio.create_task(owner.close(final=False))
                await stop_started.wait()

                replacement = await asyncio.wait_for(owner.ensure(), timeout=1)

                stop_release.set()
                await shutdown

        assert replacement is not stopping


# --- Only the owner runs Band MCP backends -----------------------------------

_OWNER_PACKAGE = SRC_ROOT / "integrations" / "mcp"
_SCAN_ROOTS = (SRC_ROOT, REPO_ROOT / "packages" / "band-mcp" / "src")


def _runs_a_backend_by_hand(source: str) -> bool:
    """Whether ``source`` creates or constructs a Band MCP server itself."""
    for node in ast.walk(ast.parse(source)):
        match node:
            case (
                ast.Name(id="create_band_mcp_backend")
                | ast.alias(name="create_band_mcp_backend")
            ):
                return True
            case ast.Call(func=ast.Name(id="LocalMCPServer")):
                return True
    return False


def test_only_the_owner_runs_band_mcp_backends() -> None:
    """A new adapter goes through SharedBandMCPBackend, which starts, replaces
    and stops its server; hand-rolling any of that is how a crash goes unseen."""
    assert all(root.is_dir() for root in (*_SCAN_ROOTS, _OWNER_PACKAGE))
    offenders = sorted(
        path.relative_to(REPO_ROOT)
        for root in _SCAN_ROOTS
        for path in root.rglob("*.py")
        if not path.is_relative_to(_OWNER_PACKAGE)
        and _runs_a_backend_by_hand(path.read_text(encoding="utf-8"))
    )

    assert not offenders, (
        f"{[str(path) for path in offenders]} run a Band MCP backend by hand; "
        "hold a SharedBandMCPBackend and declare BandMCPBackendSettings instead."
    )


@pytest.mark.parametrize(
    ("source", "by_hand"),
    [
        ("from band.integrations.mcp.backends import create_band_mcp_backend", True),
        ("server = LocalMCPServer(name='band', tool_registrations=[])", True),
        ("backend = await self._mcp.ensure()", False),
    ],
)
def test_the_guard_recognizes_a_hand_rolled_backend(source: str, by_hand: bool) -> None:
    assert _runs_a_backend_by_hand(source) is by_hand
