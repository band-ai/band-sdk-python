"""Native OMP startup fixtures."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
)
from band.testing import FakeAgentTools
from tests.adapters.omp.askkit import room_ask_adapter
from tests.adapters.omp.completion import (
    HoldInput,
    RefreshClients,
    RetirementClients,
    RetirementContext,
    RetirementTurn,
    StubbornAdapterTool,
    SurvivingToolContext,
    SurvivingToolTurn,
)
from tests.adapters.omp.custody import owned_room
from tests.adapters.omp.lifecyclekit import Launches
from tests.adapters.omp.mailboxpeer import MODEL_PROVIDER, ORIGINAL_MODEL
from tests.adapters.omp.modelpeer import NativeFault
from tests.adapters.omp.models import SelectingResolver
from tests.adapters.omp.runtime import AdapterFactory, PendingRuntime
from tests.adapters.omp.selectionkit import (
    SELECTED_EFFORT,
    OverlappingSelections,
    RollbackRuntime,
)
from tests.adapters.omp.turnpeer import TurnControl
from tests.adapters.omp.turns import (
    NativeCallbacks,
    NativeTurn,
    NativeTurnFactory,
    run_turn,
)
from tests.integrations.omprpc.completion import NO_CALLBACK_GRACE_S, snapshots_observed
from tests.integrations.omprpc.gate import process_gate_session
from tests.integrations.omprpc.spawn import OMP_PEER_COMMAND
from tests.integrations.ompscenarios import PeerScenario
from tests.paths import REPO_ROOT
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock


@pytest_asyncio.fixture(loop_scope="function")
async def adapter_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, native_startup_ready
) -> AsyncIterator[AdapterFactory]:
    async with AsyncExitStack() as resources:

        def create(
            runtime: PendingRuntime,
            *,
            config: OmpAdapterConfig | None = None,
            **features: Any,
        ) -> OmpAdapter:
            monkeypatch.setattr(
                "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
            )
            adapter = OmpAdapter(
                config or OmpAdapterConfig(cwd=str(tmp_path)), **features
            )
            resources.push_async_callback(adapter.cleanup_all)
            return adapter

        yield create


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch, native_startup_ready) -> Launches:
    record = Launches()
    monkeypatch.setattr("band.adapters.omp.lifecycle.OmpRpcClient", record)
    return record


@pytest.fixture
def native_turn(tmp_path: Path) -> NativeTurnFactory:
    @asynccontextmanager
    async def open_turn(
        *,
        config: OmpAdapterConfig | None = None,
        tools: FakeAgentTools | None = None,
        **features: Any,
    ) -> AsyncIterator[NativeTurn]:
        async with process_gate_session() as gate:
            base = config or OmpAdapterConfig(cwd=str(tmp_path))
            config = base.model_copy(
                update={
                    "command": (
                        sys.executable,
                        "-u",
                        "-m",
                        "tests.adapters.omp.turnpeer",
                        "--gate-port",
                        str(gate.port),
                    ),
                    "env": {**(base.env or {}), "PYTHONPATH": str(REPO_ROOT)},
                }
            )
            adapter = OmpAdapter(config, **features)
            tools = tools if tools is not None else FakeAgentTools()
            task = asyncio.create_task(run_turn(adapter, tools))
            ready = asyncio.create_task(gate.connection())
            try:
                await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
                if task.done():
                    await task
                await ready
                client = owned_room(adapter, "room-123").client
                yield NativeTurn(adapter, tools, client, NativeCallbacks(client))
                await client.request(TurnControl.FINISH)
                await task
            finally:
                ready.cancel()
                task.cancel()
                await asyncio.gather(ready, task, return_exceptions=True)
                await adapter.cleanup_all()

    return open_turn


@pytest.fixture
def native_startup_ready(monkeypatch):
    ready = AsyncMock()
    monkeypatch.setattr("band.adapters.omp.lifecycle.omp_mcp_ready", ready)
    return ready


@pytest.fixture
async def overlapping_selections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, native_startup_ready
) -> AsyncIterator[tuple[OmpAdapter, RollbackRuntime, OverlappingSelections]]:
    runtime = RollbackRuntime()
    resolver = OverlappingSelections(runtime)
    monkeypatch.setattr(
        "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
    )
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            model=f"{MODEL_PROVIDER}/{ORIGINAL_MODEL}",
            reasoning_effort=SELECTED_EFFORT,
        ),
        resolve_permission=resolver,
        emit=(),
    )
    resolver.adapter = adapter
    try:
        yield adapter, runtime, resolver
    finally:
        resolver.release.set()
        runtime.release_rollback.set()
        await adapter.cleanup_all()


@pytest.fixture
def ask_adapter(tmp_path: Path) -> OmpAdapter:
    """An adapter whose native peer raises a structured ask dialog."""
    return room_ask_adapter(tmp_path)


@pytest.fixture
def refresh_clients(
    monkeypatch: pytest.MonkeyPatch, native_startup_ready
) -> RefreshClients:
    clients = RefreshClients()
    monkeypatch.setattr("band.adapters.omp.lifecycle.OmpRpcClient", clients)
    return clients


@pytest_asyncio.fixture(loop_scope="function")
async def selection_adapter(
    tmp_path: Path,
) -> AsyncIterator[tuple[OmpAdapter, SelectingResolver]]:
    resolver = SelectingResolver()
    adapter = OmpAdapter(
        OmpAdapterConfig(
            cwd=str(tmp_path),
            model="openai/original",
            inject_band_tools=False,
            command=(sys.executable, "-u", "-m", "tests.adapters.omp.selectionpeer"),
            env={
                "PYTHONPATH": str(REPO_ROOT),
                "OMP_TEST_PERMISSION_TITLE": "Approve tool?",
                "OMP_TEST_NATIVE_NAME": "mcp__band_send_message",
                "OMP_TEST_NATIVE_ARGUMENTS": '{"content":"Selected model reply"}',
                "OMP_TEST_NATIVE_ERROR": "0",
            },
        ),
        resolve_permission=resolver,
        emit=(),
    )
    resolver.adapter = adapter
    try:
        yield adapter, resolver
    finally:
        await adapter.cleanup_all()


@pytest_asyncio.fixture(loop_scope="function")
async def real_adapter(tmp_path: Path) -> AsyncIterator[AdapterFactory]:
    async with AsyncExitStack() as resources:

        def create(
            *,
            config: OmpAdapterConfig | None = None,
            faults: tuple[NativeFault, ...] = (),
            session_file: str | None = None,
            **features: Any,
        ) -> OmpAdapter:
            base = config or OmpAdapterConfig(cwd=str(tmp_path))
            command = [sys.executable, "-u", "-m", "tests.adapters.omp.modelpeer"]
            for fault in faults:
                command.extend(("--fault", fault))
            if session_file is not None:
                command.extend(("--session-file", session_file))
            native = base.model_copy(
                update={
                    "command": tuple(command),
                    "env": {**(base.env or {}), "PYTHONPATH": str(REPO_ROOT)},
                }
            )
            adapter = OmpAdapter(native, **features)
            resources.push_async_callback(adapter.cleanup_all)
            return adapter

        yield create


@pytest.fixture
def surviving_tool_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_startup_ready
) -> SurvivingToolContext:
    @asynccontextmanager
    async def open_turn() -> AsyncIterator[SurvivingToolTurn]:
        clients = RetirementClients()
        monkeypatch.setattr("band.adapters.omp.lifecycle.OmpRpcClient", clients)
        hold = StubbornAdapterTool()
        adapter = OmpAdapter(
            OmpAdapterConfig(
                command=(
                    *OMP_PEER_COMMAND,
                    "--scenario",
                    PeerScenario.SETTLED_WITH_UNFINISHED_CUSTOM_TOOL,
                )
            ),
            workspace_for_room=lambda _room: str(tmp_path),
            emit=(),
            additional_tools=[(HoldInput, hold.hold)],
        )
        tools = FakeAgentTools()
        try:
            async with background(run_turn(adapter, tools)) as running:
                try:
                    runtime = await clients.created.get()
                    await hold.entered.wait()
                    await snapshots_observed(runtime, count=2)
                    runtime.callback_exit_timeout_s = NO_CALLBACK_GRACE_S
                    yield SurvivingToolTurn(
                        adapter, clients, hold, runtime, running, tools
                    )
                finally:
                    await hold.release()
        finally:
            await adapter.cleanup_all()

    return open_turn()


@pytest.fixture
def retirement_turn(surviving_tool_turn: SurvivingToolContext) -> RetirementContext:
    @asynccontextmanager
    async def open_turn() -> AsyncIterator[RetirementTurn]:
        async with controlled_clock() as clock:
            resources = AsyncExitStack()
            try:
                turn = await clock.run_io(
                    resources.enter_async_context(surviving_tool_turn)
                )
                yield RetirementTurn(
                    turn.adapter,
                    turn.clients,
                    turn.hold,
                    turn.runtime,
                    turn.running,
                    turn.tools,
                    clock,
                )
            finally:
                await clock.run_io(resources.aclose())

    return open_turn()
