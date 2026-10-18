"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import nullcontext

import looptime
import pytest
import pytest_asyncio
from typing_extensions import Unpack

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import FeatureKwargs
from band.runtime.custom_tools import CustomToolDef
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import AskFactory, BandMCPCalls, FakeOpencodeClient
from tests.mcpbackends import BackendStarts, backends_created_by


@pytest.fixture
def asks() -> AskFactory:
    """Builds OpenCode permission and question asks for a test."""
    return AskFactory()


@pytest.fixture
def tools() -> FakeAgentTools:
    """Fresh platform tools for one adapter test."""
    return FakeAgentTools()


@pytest_asyncio.fixture(loop_scope="function")
async def make_adapter(
    fake_band_mcp_backends: BackendStarts,
) -> AsyncIterator[Callable[..., OpencodeAdapter]]:
    """Build an adapter around the scenario's fake OpenCode client."""

    owned: list[tuple[OpencodeAdapter, bool]] = []
    loop = asyncio.get_running_loop()

    def build(
        client: FakeOpencodeClient,
        *,
        config: OpencodeAdapterConfig | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        adapter_type: type[OpencodeAdapter] = OpencodeAdapter,
        **features: Unpack[FeatureKwargs],
    ) -> OpencodeAdapter:
        adapter = adapter_type(
            config=config,
            additional_tools=additional_tools,
            client_factory=lambda _: client,
            **features,
        )

        owned.append(
            (adapter, isinstance(loop, looptime.LoopTimeEventLoop) and loop.looptime_on)
        )
        return adapter

    try:
        yield build
    finally:
        for adapter, virtual in reversed(owned):
            mode = (
                looptime.enabled(strict=True)
                if virtual
                and isinstance(loop, looptime.LoopTimeEventLoop)
                and not loop.looptime_on
                else nullcontext()
            )
            with mode:
                watchers = [
                    room.turn_task for room in adapter._rooms.values() if room.turn_task
                ]
                try:
                    for room_id in list(adapter._rooms):
                        await adapter.on_cleanup(room_id)
                finally:
                    await asyncio.gather(*watchers, return_exceptions=True)
                assert adapter._rooms == {}
                assert adapter._registration is None
                assert adapter._event_task is None


@pytest.fixture(autouse=True)
def fake_band_mcp_backends() -> Iterator[BackendStarts]:
    """Fake every Band MCP backend start in OpenCode adapter tests."""
    with backends_created_by() as starts:
        yield starts


@pytest.fixture
def mcp_backend(fake_band_mcp_backends: BackendStarts) -> BandMCPCalls:
    """Band tool calls the model makes over the adapter's MCP backend."""
    return BandMCPCalls(fake_band_mcp_backends)
